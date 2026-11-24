# 05. Test ve Değerlendirme

Bu projede "test" tek bir şey değil. Aşağıdaki katmanların her biri **farklı bir soruya** cevap
verir. Hangi soruya hangi aracın cevap verdiğini bilmek, junior ile mid-level arasındaki en
önemli farklardan biri.

| Katman | Cevapladığı soru | Nerede | Stack gerekir mi? |
|---|---|---|---|
| Unit test | "Bu fonksiyon/sınıf kendi başına doğru mu?" | `*/tests/` (kodun yanında) | Hayır |
| Architecture test | "Mimari kurallar çiğnendi mi?" | `tests/architecture/` | Hayır |
| Integration test | "Gerçek servisler birlikte çalışıyor mu? DB gerçekten reddediyor mu?" | `tests/integration/` | **Evet** (`up`) |
| Chaos senaryo testleri | "Arıza enjekte eden araç gerçekten doğru arızayı üretiyor mu?" | `company/chaos/tests/` | Evet (canlı DB ve servisler; yoksa atlanır) |
| Eval | "Asistan uçtan uca **doğru davranıyor mu**?" | `evals/` | Evet (`up`) |

## 1. Unit testler

"Kodun yanında" kuralı: bir paketin testleri kendi `tests/` klasöründe. Örnekler:

- `assistant/policy/tests/test_engine.py`: policy motoru (default-deny, koşullar, tutar sınırı).
- `assistant/audit/tests/test_hash_chain.py`, `test_audit_concurrency.py`: zincir ve serileştirme.
- `assistant/modes/tests/` (`test_machine.py`, `test_router.py`, `test_investigation.py`,
  `test_turn_scoping.py`, `test_pii.py`...): konuşma akışı, `FakeGateway` ve `ScriptedProvider` ile.
- `assistant/privacy/tests/`, `assistant/llm/tests/`, `assistant/api/tests/`...
- `company/*/tests/`, `integrations/mcp_*/tests/`: şirket servisleri ve adapter'lar.

Unit testlerde gerçek model ve gerçek MCP **yok**. `assistant/llm/scripted.py::ScriptedProvider`
önceden yazılmış cevaplar döner; eşleşmeyen bir prompt gelirse `UnscriptedPromptError` ile
**yüksek sesle** patlar (uydurma bir varsayılan döndürmez). `assistant/mcp_gateway/fake.py::FakeGateway`
aynı yüzeyi sahte veriyle sağlar. Bunlar mümkün çünkü kod arayüze bağımlı (bkz. sözlük:
dependency inversion).

Not: Anthropic sağlayıcısının tool yolu şu an yalnızca mock ile test ediliyor
(bkz. `07-bilinen-sinirlar.md`).

## 2. Architecture testleri

`tests/architecture/test_boundaries.py` üç test içerir:

1. `test_import_boundaries`: AST ile hangi dünyanın neyi import ettiğine bakar.
2. `test_company_code_never_mentions_the_assistant`: `company/` içinde yasaklı kelimeleri arar.
3. `test_assistant_never_hardcodes_company_endpoints`: asistan kodunda sabit şirket adresi arar.

Hızlıdır ve Docker gerektirmez. Bir PR'a başlamadan önce ve bitirdikten sonra çalıştır:

```powershell
docker compose run --rm test-runner python -m pytest tests/architecture
# veya yerelde: python -m pytest tests/architecture   (pytest-timeout kurulu değilse: -o addopts="")
```

## 3. Integration testleri

`tests/integration/` gerçek çalışan stack'e HTTP/SQL ile bağlanır (`conftest.py` içindeki
`ServiceProbe`). Stack ayakta değilse testler **atlanır** (`skip`), kırılmaz; bu yüzden `make test`
stack'siz de işe yarar. Önemli testler:

- `test_readonly_role.py`: `readonly_diag` ile `diag.*` okunabilir, `core.*` erişilemez, her yazma reddedilir,
  view'larda kimlik sütunu yoktur.
- `test_audit_append_only.py`: zincir doğrulanır ve DB `UPDATE`/`DELETE`'i reddeder (`"append-only"`).
- `test_stack_health.py`: servisler ayakta ve seed var.

```powershell
docker compose up -d --build
docker compose run --rm test-runner python -m pytest tests/integration -m integration
```

## 4. Neden testler suite suite çalışır? (`scripts/run_tests.sh`)

Her şirket servisinin Python paketi **aynı adı** taşır: `app`. Tek bir pytest sürecinde
`company/core-api/app` ve `company/ticketing/app` aynı anda import edilemez; `import app`
ilk bulduğunu alır, diğerleri çalışmaz veya yanlış modülü test eder. Çözüm: `scripts/run_tests.sh`
her suite'i **ayrı süreçte**, doğru `PYTHONPATH` ile çalıştırır:

1. `tests/architecture`
2. her `company/*/tests`
3. her `integrations/mcp_*/tests` ve `integrations/common/tests`
4. `assistant` (tüm paketler tek süreçte; burada isimler çakışmaz)
5. `evals/tests`
6. `tests/integration`

Bir suite kırılsa bile diğerleri çalışır; sonunda `FAILED SUITES: ...` listesi çıkar. Bu yüzden
tek bir yerden `pytest company` çalıştırmayı **deneme**: çoğunlukla yanıltıcı hatalar alırsın.
Tek bir suite çalıştırmak için `PYTHONPATH`'i o suite'in beklediği gibi kurman gerekir
(`scripts/run_tests.sh` içindeki satırlara bak). Örneğin sadece policy testleri:

```powershell
docker compose run --rm test-runner env PYTHONPATH=/workspace/assistant python -m pytest assistant/policy
```

Dikkat: `run_tests.sh`'e `-k ...` gibi bir filtre verirsen her suite'e iletilir; eşleşen test
bulamayan suite pytest'ten "hiç test toplanmadı" kodu döner ve script onu **başarısız** sayar.
Filtre için yukarıdaki gibi tek suite komutunu kullan.

## 5. Eval'ler (`evals/`)

Eval = "asistan **doğru davranıyor mu**?" sorusunu veriyle ölçen düzenek. Unit test tek bir
fonksiyonu sınar; eval gerçek `Orchestrator`'ı, gerçek `ToolGateway`'i (MCP adapter'larına
bağlı) ve gerçek şirket servislerini çalıştırır.

```powershell
docker compose run --rm test-runner python -m evals.run --report evals/reports
# parçalar:
docker compose run --rm test-runner python -m evals.run --only advisory --report evals/reports
docker compose run --rm test-runner python -m evals.run --only scenarios --case double_charge
```

Veri: `evals/datasets/scenarios.yaml` (6 chaos senaryosu x farklı tonlar, 3 "aksiyonsuz" vaka,
3 investigation vakası) ve `evals/datasets/advisory_profiles.yaml` (10 müşteri profili). Bu
commit'te scripted eval toplam **37** vaka raporlar: 27 senaryo vakası (20 ton varyantı mesajı +
regional outage'ın "iki müşteri tek ticket" kontrolü + 3 aksiyonsuz + 3 investigation) + 10
advisory profili.

### Vakalarda neler kontrol edilir?

`expect:` bloğundaki anahtarlar (tam açıklamaları `evals/README.md` içinde):

| Anahtar (örnek) | Ne ölçer |
|---|---|
| `final_mode`, `issue_type`, `scope` | Doğru mode ve doğru teşhis |
| `action_executed` | `asst.action_records` içinde o aksiyon **gerçekten** çalıştı mı (cevap metnine bakmaz) |
| `policy_denied_action` | Policy reddetti mi |
| `ticket_department`, `must_not_create_ticket` | Doğru departman / gereksiz ticket yok |
| `ticket_evidence_must_include_payment_ids` | Ticket kanıtı yeterli mi (örn. çift çekimde iki ödeme id'si) |
| `reply_must_ask_question` | Investigation'ın sorusu gerçekten soru mu |
| `must_not_execute_action` | Selamlama gibi akışlar aksiyon hattına hiç girmedi |
| advisory: `expected_best`, `must_not_recommend`, `invariants.deterministic` | Doğru paket, yasak paket yok, aynı girdi = aynı çıktı |

Sonuçlar `PASS` / `FAIL` / `ERROR` olur. **`ERROR`** "asistan yanlış yaptı" demek değil, "ölçemedim"
demek (chaos CLI çalışmadı, adapter yok...) ve sessizce geçmez. Çıkış kodu yalnızca her vaka
`PASS` ise 0'dır; yani CI kapısı olarak kullanılabilir.

### Scripted vs. live

| | `EVAL_MODE=scripted` (varsayılan) | `EVAL_MODE=live` |
|---|---|---|
| Model | `ScriptedProvider`: her konuşmanın **ilk mesajı** için tek bir hazır cevap | `llm.factory.get_provider()` ile gerçek model |
| API anahtarı | Gerekmez | Gerekir |
| Tekrarlanabilir mi | Evet | Hayır (model değişir) |
| Neyi ölçer | **Tesisatı**: state machine, policy, gateway, audit, ticket akışı | Tesisat **ve** model kalitesi |

> **Ders: "34/34 geçti" cümlesi neyi söyler?** Scripted modda `classify_intent` cevabını, bazı
> kararların güvenini ve investigation'ın tool çağrılarını **biz yazdık** (`evals/fixtures.py`,
> dataset'teki `scripted_investigation`). Dolayısıyla "hepsi geçti" şunu kanıtlar: *eğer model
> beklenen şeyi söylerse, geri kalan sistem doğru davranıyor.* Şunu kanıtlamaz: *gerçek model
> beklenen şeyi söyler.* Yani scripted eval bir **regresyon ağı**dır (birisi policy'yi veya
> state machine'i bozarsa yakalar), model kalitesi ölçümü değil. Bir yazılı rapora "34/34" yazarken
> hangi modda koştuğunu **her zaman** yaz. (`evals/README.md` ayrıca `live` modunun bu repoda
> koşulmadığını ve bazı yolların kapsanmadığını dürüstçe listeliyor: "Honest gaps".)

Küçük ama öğretici bir ayrıntı: `payment_system_down` için bazı kararlar bilerek **scripted
bırakılmadı**, böylece güven eşiğinin altına düşünce deterministik yedek tabloya düşme yolu da
uçtan uca çalışıyor (`evals/README.md`, "What is — and isn't — exercised").

## 6. Chaos senaryoları

`company/chaos/` şirketin sistemine kasıtlı arıza enjekte eder. Hem demo hem eval verisi
kaynağı. Senaryolar: `stuck_provisioning`, `paid_not_active`, `regional_outage`, `double_charge`,
`missed_installation`, `payment_down`.

```powershell
docker compose run --rm chaos python -m chaos.cli stuck_provisioning
docker compose run --rm chaos python -m chaos.cli status
docker compose run --rm chaos python -m chaos.cli reset
# bölge belirtmek için:
docker compose run --rm chaos python -m chaos.cli regional_outage --region LDN-CAM
# dry run (hiçbir şey yazmaz):
docker compose run --rm chaos python -m chaos.cli stuck_provisioning --dry-run
```

Her senaryodan sonra `reset` çalıştır; yoksa sonraki testler kirli veriyle karşılaşır.
`company/chaos/tests/` bu aracın kendi testleri: enjekte ediyor mu, `reset` temizliyor mu?

## 7. Flaky testler: bu projede gördüklerimiz ve nasıl düşünmeli

**Flaky test**: kod değişmediği hâlde bazen geçen bazen kalan test. En tehlikeli test türüdür,
çünkü takım kırmızıya alışır ve gerçek hatayı kaçırır. Projede iki tekrar eden kalıp gözlendi:

**Kalıp 1: Suite'ler aynı şirket veritabanını paylaşıyor.**
Chaos testleri, core-api testleri ve eval'ler aynı `company-db` üzerinde çalışır ve chaos
senaryoları gerçek satırları değiştirir (iş durdurur, çift ödeme yazar...). Bir suite'in
bıraktığı durum başka bir suite'in varsayımını bozabilir: örneğin chaos "kurban" müşteri seçerken
önceden aynı müşteriyi kullanan bir test onun durumunu değiştirmiş olabilir. Tek başına geçen test
tam koşuda kırılırsa ilk şüphe **paylaşılan durum**dur.
- Düşünme biçimi: "Bu test hangi verinin var olduğunu varsayıyor? Onu kim değiştirebilir?"
- Çözüm yönü: testin kendi verisini yaratması (kurban seçme mantığı `ensure_candidate` bu yüzden var),
  işi bitince temizlemesi, ya da suite'leri ayrı veritabanı/şema ile izole etmesi.

**Kalıp 2: Testler startup'tan hemen sonra provisioning worker ile yarışıyor.**
`provisioning-worker` arka planda döngü halinde iş alır (`WORKER_INTERVAL_SECONDS=5`,
`.env.example`). Stack yeni kalktıysa worker kuyruktaki işleri alıp durumlarını değiştirebilir;
bir test "iş `queued` olmalı" derken worker onu çoktan `running` yapmış olabilir.
- Düşünme biçimi: "Zamanlamaya bağlı bir varsayımım var mı? Arka planda başka bir aktör bu veriyi
  değiştiriyor mu?"
- Çözüm yönü: sabit `sleep` değil **koşul bekleme** (poll with timeout), chaos'un kullandığı
  `chaos_hold` benzeri "tutma" işaretleri, veya worker'ı test için durdurmak.

Genel kural: Flaky bir testi `retry` ile **susturma**. Önce şunu sor: gerçek bir hata mı (bir
yarış durumu uygulama kodunda mı)? Yoksa test mi hatalı? Her ikisi de gerçek olabilir. Bu
projedeki yield/commit olayı (bkz. sözlük, "Yield dependency") flaky bir testin **gerçek bir
uygulama hatasının belirtisi** olabileceğinin iyi bir örneği.

## 8. Test yazarken kontrol listesi

- [ ] Test, neyi garanti ettiğini adında söylüyor mu? (`test_unknown_action_is_denied`)
- [ ] Gerçek model/ağ gerekiyor mu? Gerekmiyorsa `ScriptedProvider`/`FakeGateway` kullan.
- [ ] Başka bir testin bıraktığı veriye bağımlı mı?
- [ ] Sabit `sleep` yerine koşul beklemesi mi kullanıyorum?
- [ ] `tests/architecture` hâlâ geçiyor mu?
- [ ] Yeni bir davranış mı ekledim? O zaman bir unit test **ve** (kullanıcıya görünen davranışsa)
      bir eval vakası düşün.
