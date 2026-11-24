# 06. Alıştırmalar

13 alıştırma var: 4 Kolay, 6 Orta, 3 Zor. Her birinde **amaç**, **nereden başla**, **ipuçları** ve
**kabul kriterleri** (bitti demek için neyin geçmesi/eklenmesi gerekir) bulunur.

Genel kurallar:

1. Her alıştırmayı ayrı bir git branch'inde yap (`git switch -c ex/01-...`).
2. Başlamadan önce ve bittiğinde `docker compose run --rm test-runner python -m pytest tests/architecture`
   çalıştır; hâlâ yeşil olmalı.
3. Kod ve yorumlar **İngilizce**, `docs/STYLE.md`'ye uygun. Bu rehber Türkçe.
4. Testleri `scripts/run_tests.sh` ile suite suite koş (bkz. `05-test-ve-degerlendirme.md`).
5. Takıldığında önce ilgili dosyanın en üstündeki öğretim başlığını (`Read next:`) oku.

> Sürüm notu: Alıştırmalar bu commit'in durumuna göre yazıldı. Kod ile çelişen bir ipucu görürsen
> kod haklıdır; bir düzeltme PR'ı açabilirsin.

| # | Başlık | Seviye |
|---|---|---|
| 1 | Mimari sınırı bilerek ihlal et | Kolay |
| 2 | `ConversationState`'e tipli bir alan ekle | Kolay |
| 3 | Yeni bir şikâyet için eval vakası yaz | Kolay |
| 4 | Policy'ye koşullu yeni bir aksiyon ekle | Kolay |
| 5 | Investigation allowlist'ine salt-okunur bir tool ekle | Orta |
| 6 | Türkiye'ye özel PII dedektörleri | Orta |
| 7 | Türkçe locale (`replies.py` dikişi) | Orta |
| 8 | Config tutarlılık testi: bulunan hatayı oku, testi genişlet | Orta |
| 9 | "Tek başına geçer, tam koşuda kırılır": yazılı analiz | Orta |
| 10 | Yield/commit düzeltmesini oku, doğrula ve yakalayan bir test yaz | Orta |
| 11 | Yeni bir chaos senaryosu + eval vakası | Zor |
| 12 | Sahte streaming yerine gerçek token streaming | Zor |
| 13 | Açık investigation sırasında konu değişimi | Zor |

---

## 1. Mimari sınırı bilerek ihlal et (Kolay)

**Amaç.** Sınır testlerinin gerçekten bir şey yakaladığını görmek; hata mesajını okumayı öğrenmek.

**Nereden başla.** `tests/architecture/test_boundaries.py`, `assistant/modes/router.py`.

**İpuçları.**
- `assistant/modes/router.py` içine geçici olarak `import shared` yaz ve `tests/architecture`
  testlerini çalıştır. Hangi test kırıldı, mesaj ne söylüyor?
- Sonra `company/core-api/app/db.py` içine bir yorum satırı olarak `# TODO: ask the assistant` yaz.
  Hangi test kırıldı ve neden bir *yorum* bile yasak? (Cevap: regex her metni tarar, çünkü kural
  "şirket asistanı hiç bilmez" idi.)
- Üçüncü deneme: `assistant/` içinde `url = "http://core-api:8000"` yaz (docstring/yorum dışında).

**Kabul kriterleri.**
- Üç deneyin her biri için "hangi test, hangi mesaj" notu (kısa bir yazı).
- Üç değişiklik **geri alındı**, `tests/architecture` yeşil (3 passed).

---

## 2. `ConversationState`'e tipli bir alan ekle (Kolay)

**Amaç.** Tipli durum ve geriye uyumluluğu (eski satırların yüklenmesi) pratik etmek.

**Nereden başla.** `assistant/modes/state.py`, `assistant/modes/tests/test_state.py`,
`assistant/modes/conversation_store.py`.

**İpuçları.**
- Örneğin `topic_changes: int = 0` ekle (konu değiştirme sayısı). `Field(default=..., description=...)`
  stiline uy.
- Alanın varsayılanı eski veritabanı satırlarında alan olmayınca devreye girer. `extra="ignore"`
  neden gerekli?
- `test_round_trip_preserves_every_field` testini yeni alanı kapsayacak şekilde güncelle ve eski
  satır testine (legacy row) dokun: alan yokken varsayılan geliyor mu?

**Kabul kriterleri.**
- `assistant/modes/tests/test_state.py` yeni alanı hem round-trip hem eski satır senaryosunda kapsar.
- `docker compose run --rm test-runner env PYTHONPATH=/workspace/assistant python -m pytest assistant/modes/tests`
  yeşil.
- Alanın bir yerde kullanıldığını gösterebiliyorsun (örn. `dispatch.py` içinde sayıyı artırıyorsun) ya da
  neden henüz kullanılmadığını PR açıklamasında yazıyorsun.

---

## 3. Yeni bir şikâyet için eval vakası yaz (Kolay)

**Amaç.** Eval veri setinin yapısını öğrenmek ve "scripted eval neyi ölçer?" sorusunu somutlaştırmak.

**Nereden başla.** `evals/datasets/scenarios.yaml` (`investigation_cases` bölümü, `investigation_slow_evenings`
örneği), `evals/README.md`, `evals/tests/test_investigation_cases.py`, `evals/fixtures.py`.

**İpuçları.**
- Yeni şikâyet: "My wifi drops out every time it rains." Model önce `get_modem_status` ve
  `get_region_health` çağırsın, sonra bir `escalate` bulgusu ile `TECHNICAL_INFRA`'ya ticket versin
  (ya da `needs_more_info` ile bir soru sorsun).
- Tool'ların `routing.yaml: investigation_tools` içinde olduğundan emin ol.
- `expect:` bloğunda `final_mode`, `must_not_execute_action`, uygun ticket/soru kontrolleri olsun.
- Çalıştır: `docker compose run --rm test-runner python -m evals.run --only scenarios --case investigation_wifi_rain`
  (stack ayakta olmalı).

**Kabul kriterleri.**
- Vaka PASS. `evals/tests/` altındaki saf testler yeşil.
- PR açıklamasında şunu yaz: "Bu vaka scripted modda **neyi** kanıtlıyor, neyi kanıtlamıyor?"

---

## 4. Policy'ye koşullu yeni bir aksiyon ekle (Kolay)

**Amaç.** Yetkinin veri olarak tanımlanmasını, default-deny'ı ve koşul operatörlerini deneyimlemek.

**Nereden başla.** `config/tenants/netswift/policy.yaml`, `assistant/policy/engine.py`,
`assistant/policy/tests/test_engine.py` (`MATRIX` tablosu), `assistant/core_common/config_models.py`
(`Condition`, `ActionPolicy`).

**İpuçları.**
- Varsayımsal bir aksiyon ekle: `schedule_callback` (müşteriye geri arama): `allowed: true`,
  `requires_confirmation: false`, koşul `subscription.status in [active]`, ve
  `rate_limit: {per_conversation: 1}`.
- `Condition` modeli `extra="forbid"`: yaml'da yazım hatası yaparsan config **yüklenirken** hata alırsın.
  Bunu bir kez kasten dene.
- `test_engine.py`'deki `MATRIX`'e satırlar ekle: izin verilen durum, koşul sağlanmayan durum,
  rate limit aşıldı, ve `subscription` verisi hiç yok (bu durumda koşul **sağlanmadı** sayılmalı: neden?).
- Düşün: Bu aksiyonun gerçekten çalışması için başka nerelere dokunmak gerekir?
  (İpucu: `assistant/mcp_gateway/action_runner.py::ACTION_TOOL_MAP` ve bir MCP tool'u. Eşleme yoksa
  `UnmappedActionError` olur.) Bu alıştırmada sadece policy katmanını yapıyorsun.

**Kabul kriterleri.**
- `assistant/policy/tests/` yeşil ve yeni aksiyon için en az 4 test satırı var.
- `tests/architecture` yeşil.
- PR açıklamasında "çalışması için eksik halkalar" listesi.

---

## 5. Investigation allowlist'ine salt-okunur bir tool ekle (Orta)

**Amaç.** Least privilege'ı pratik etmek: modelin gücünü artırırken sınırı korumak.

**Nereden başla.** `config/tenants/netswift/routing.yaml` (`investigation_tools`),
`assistant/modes/investigation/tools.py` (`available_tools`), `assistant/core_common/config_models.py`
(`TenantConfig._investigation_tools_are_read_only`), `assistant/modes/tests/test_investigation.py`.

**İpuçları.**
- `list_packages` mcp-core tarafından zaten sunuluyor (`integrations/mcp_core/app/tools_read.py`)
  ama allowlist'te yok. "Yavaş internet" şikâyetlerinde modelin müşterinin paket hızını
  katalogla karşılaştırması faydalı olur mu? Ekle.
- Allowlist'e `retry_provisioning_job` yazmayı dene. Ne oluyor ve **nerede**? (Config yüklenirken
  doğrulama yakalar; neden çalışma zamanında beklemiyoruz?)
- `FakeGateway` ile `test_investigation.py` içindeki testlere bakıp yeni tool'un modele
  sunulduğunu gösteren bir test yaz.
- Zor ek görev: Yeni bir `diag.*` view'u (`company/core-api/app/bootstrap.py`) ve bunu okuyan yeni bir
  mcp-core tool'u ekle. `readonly_diag`'in view'a otomatik SELECT hakkı olduğuna (`ALTER DEFAULT
  PRIVILEGES`) dikkat et ve view'da PII sütunu olmadığını göster (`tests/integration/test_readonly_role.py`).

**Kabul kriterleri.**
- Yeni tool modele sunuluyor (test), yazma tool'u listeye eklenince config yüklenmiyor (test).
- `assistant/modes/tests` yeşil.
- Neden bu tool'un tenant başına ayarlanabilir olduğunu tek paragrafla açıklıyorsun.

---

## 6. Türkiye'ye özel PII dedektörleri (Orta)

**Amaç.** Maskeleme katmanını genişletmek; "dar dedektör" ilkesini ve doğrulama algoritmalarını
(checksum) öğrenmek.

**Nereden başla.** `assistant/privacy/detectors.py` (`find_card_numbers` + `luhn_is_valid` örneği),
`assistant/privacy/masking.py` (`mask_text` ve `assert_no_pii`), `assistant/privacy/tests/`.

**Eklenecekler.**
1. **T.C. kimlik no** (11 haneli). İlk hane 0 olamaz. 10. hane =
   `((d1+d3+d5+d7+d9)*7 - (d2+d4+d6+d8)) mod 10`, 11. hane = `(d1+...+d10) mod 10`.
2. **+90 telefon** (örn. `+90 532 123 45 67`, `0532 123 45 67`).
3. **TR IBAN** (`TR` + 2 kontrol hanesi + 5 hane banka + 1 rezerve + 16 hane hesap = 26 karakter;
   mod-97 kontrolü).

**İpuçları.**
- Her bulucu `(start, end, cleaned_value)` listesi döner (mevcut desenle aynı).
- **Sıra önemli:** 11 haneli bir telefon (`05321234567`) ile 11 haneli bir T.C. no aynı basamak
  uzunluğundadır. Önce hangisi aranmalı? T.C. no'nun ilk hanesi 0 olamadığına göre telefondaki 0
  önek ayrım için işe yarar mı? Testlerle kanıtla.
- `mask_text` içindeki `for finder, masker in (...)` demetine ekle **ve** `assert_no_pii`
  içindeki bulucu listesine de ekle (iki liste ayrı, ikisini de güncellemezsen audit/model koruması
  yeni PII'yi kaçırır).
- Yeni "renderer" fonksiyonları (`mask_national_id_value` benzeri) yaz; yer tutucu metni mevcut
  stile uydur.
- `policy.yaml: pii.mask_before_model` listesindeki alan adlarıyla (`national_id`, `phone`, `iban`)
  ilişkisini düşün: yapısal payload'larda alan adına göre maskeleme zaten var.
- Yanlış pozitifleri test et: 11 haneli rastgele bir sipariş numarası maskelenmemeli (checksum tutmuyorsa).

**Kabul kriterleri.**
- `assistant/privacy/tests/` içinde her dedektör için pozitif, negatif (checksum tutmayan) ve
  sınır durumları (boşluklu/boşluksuz yazım) testleri.
- `assert_no_pii` ham T.C. no içeren bir metinde `PIILeakError` fırlatıyor (test).
- Mevcut İngiltere dedektörlerinin testleri bozulmadı.

---

## 7. Türkçe locale (Orta)

**Amaç.** "Wording ile logic'i ayırma" ilkesini kullanarak ürüne ikinci dil eklemek ve bunun **nerede
bittiğini** görmek.

**Nereden başla.** `assistant/modes/replies.py` (dosya başlığı bu dikişi anlatıyor),
`config/tenants/netswift/tenant.yaml` (`locale`, `persona.language`), `assistant/modes/` içindeki
`from modes import replies` kullanımları.

**İpuçları.**
- Önce `grep -rn "replies\." assistant/modes | wc -l` ile kaç yerde kullanıldığını gör. Sabitler
  ve küçük fonksiyonlar (`action_success`, `approval_question`...) var.
- Yaklaşım A: `replies_tr.py` yaz, aynı isimleri tanımla; `replies` modülünü tenant dilini okuyup
  doğru kataloğa yönlendiren bir katmana çevir. Yaklaşım B: katalogları bir paket altında topla.
  Her ikisinin artı/eksisini PR açıklamasında yaz.
- **Sınırı bul:** Aşağıdakiler `replies.py`'de **değil**: model tarafından yazılan metinler
  (investigation bulguları, advisory anlatımı), policy `reason_en` alanları, ticket gövdeleri
  (departman okuyor). Her biri için kararını yaz: çevirmek mi, İngilizce bırakmak mı?
- Testler ve eval'ler cevaplarda anahtar kelime arıyor (`reply_must_mention_any_en`). Türkçe
  katalogda bunlar nasıl çalışır?
- Tenant `locale` alanı bugün neye bağlı? (Cevap: bir alan olarak var, ama çeviri yapmıyor.)

**Kabul kriterleri.**
- Aynı konuşma tenant config'indeki bir dil ayarıyla (örn. `persona.language`) Türkçe cevap veriyor (en az router, diagnostic ve action
  cevapları).
- Bir **parite testi**: iki katalog aynı isim kümesini tanımlıyor (bir dil eksik anahtarla kalamaz).
- Dokunulmayan yerlerin listesi (neden) PR açıklamasında.

---

## 8. Config tutarlılık testi: bulunan hatayı oku, testi genişlet (Orta)

**Amaç.** "Yapılandırma ile kod arasında sessiz uyumsuzlukları" bir testle yakalamayı öğrenmek.

**Nasıl bulundu, nasıl düzeltildi.** Bir inceleme `config/tenants/_example/policy.yaml`'ın
`reschedule_installation` aksiyonuna onayla izin verdiğini, ama `ACTION_TOOL_MAP`'te bu aksiyonun
karşılığı olmadığını gösterdi: müşteri "onayla" dedikten sonra `UnmappedActionError` patlardı. Bir
adım ötede mcp-core'daki tool da partner API anahtarında `appointments:write` yetkisi olmadığı için
her zaman `SCOPE_DENIED` döner (`integrations/mcp_core/app/tools_write.py` başlığı; contracts §2.1).
İki yoldan biri seçilebilirdi: eşlemeyi tamamlayıp partner anahtarına yazma yetkisi vermek (şirketin
yetki sınırını gevşetir, `netswift` ve contracts'taki "randevuyu saha ekibi yönetir" hikâyesini
bozar) ya da policy'yi dürüst yapmak. İkincisi seçildi: `_example` artık `reschedule_installation`
için `allowed: false` + `FIELD_INSTALL`'a devir diyor. Düzeltmenin yanına
`assistant/mcp_gateway/tests/test_config_consistency.py` eklendi: her tenant için `allowed: true`
olan her aksiyonun `ACTION_TOOL_MAP`'te olduğunu doğrular; hatayı baştan yakalardı.

**Nereden başla.** `assistant/mcp_gateway/tests/test_config_consistency.py`,
`assistant/mcp_gateway/action_runner.py::ACTION_TOOL_MAP`, `config/tenants/*/policy.yaml`,
`config/tenants/_example/README.md`.

**Görev.**
1. `_example/policy.yaml`'da `reschedule_installation`'ı geçici olarak tekrar `allowed: true` yap ve
   testin kırıldığını, hata mesajının neyi söylediğini gör. Sonra geri al.
2. Testi bir adım ileri taşı: `ACTION_TOOL_MAP`'in **değerlerinin** (tool adları) gerçekten mcp-core
   veya diğer adapter'larda kayıtlı bir tool olduğunu doğrulayan ikinci bir test yaz (ipucu:
   `integrations/` ayrı bir paket; `assistant/` onu import edemez, mimari testi okuyup tool
   adlarını contracts §3 tablosundan ya da bir sabit listeden nasıl kontrol edebileceğini tasarla
   ve gerekçesini yaz).
3. Başka bir "okunmayan config" sınıfı daha ara: `config_models.py`'de tanımlı ama üretim kodunda
   hiç okunmayan bir alan var mı? (Bu bulgu turunda `routing.yaml: diagnostic_checklist` ve
   `MAX_TOOL_CALLS_PER_TURN` ayarı böyle çıktı ve kaldırıldı.) Bunu otomatik arayan küçük bir
   betik veya test tasarla.

**Kabul kriterleri.**
- İkinci test mevcut ve bulduğu her uyumsuzluk için düzeltme ya da gerekçeli istisna var.
- `assistant/` suite'i yeşil.

---

## 9. "Tek başına geçer, tam koşuda kırılır": yazılı analiz (Orta)

**Amaç.** Flaky testleri sistematik düşünmek; kod yazmadan **mühendislik analizi** yazmak.

**Nereden başla.** `05-test-ve-degerlendirme.md` bölüm 7, `scripts/run_tests.sh`,
`assistant/api/state.py` (modül düzeyinde `state`), `assistant/core_common/config.py` (config cache),
`company/chaos/tests/conftest.py` (`get_settings.cache_clear()`), `integrations/common/readonly_db.py`
(`_engines` cache).

**Görev.** 1–2 sayfalık bir yazı: "Bir test tek başına geçip tam koşuda kırılıyor. Nedenleri neler
olabilir ve hangi sırayla araştırırım?" Şunları içersin:
1. **Paylaşılan global durum** (modül düzeyinde singleton'lar, cache'ler, ortam değişkenleri): bu
   repoda en az **üç somut örnek** göster (dosya:fonksiyon ile) ve her birinin nasıl sızabileceğini anlat.
2. **Paylaşılan dış durum** (aynı `company-db`'yi kullanan suite'ler, chaos'un bıraktığı veri).
3. **Zamanlama** (arka plan worker'ı, commit'in cevaptan sonra yapılması).
4. **Sıralama**: testler sıra bağımlı mı? Nasıl kanıtlarsın? (`pytest-randomly` kurulu değil; sırayı
   ters çevirmek veya karıştırmak için ne yaparsın?)
5. Bir **araştırma planı**: hangi komutları, hangi sırayla çalıştırırsın?

**Kabul kriterleri.**
- Yazıda her iddia için bir dosya yolu veya komut var.
- Bir arkadaşın yazıyı okuyup "bir sonraki adımı" söyleyebiliyor.
- (Bonus) Küçük bir sızıntıyı gerçekten yeniden üret ve düzelt.

---

## 10. Yield/commit düzeltmesini oku, doğrula ve yakalayan bir test yaz (Orta)

**Amaç.** `ADR-10`'daki dersi kendi gözünle görmek; düzeltmenin **neden** işe yaradığını açıklamak.

**Durum.** Düzeltme uygulandı: `company/core-api/app/db.py::get_db` docstring'i dersi anlatır,
router'lar `Depends(get_db, scope="function")` kullanır, `company/requirements.txt` `fastapi>=0.165,<1.0`
aralığına bağlıdır.

**Nereden başla.** `company/core-api/app/db.py::get_db`, `company/core-api/app/routers/`,
`company/requirements.txt`, sözlükteki "Yield dependency" maddesi, `04-tasarim-kararlari.md` ADR-10.

**Görev.**
1. Düzeltmeyi oku ve kendi cümlelerinle açıkla: `scope="function"` ile varsayılan kapsam arasındaki
   fark nedir? Commit hangi anda yapılıyor, cevap hangi anda gidiyor?
2. İzole bir klasörde (repo dışında) küçük bir FastAPI uygulaması yaz: `yield` kullanan bir
   dependency, içinde `print("exit code")`; bir endpoint `print("handler done")` basıp döner; bir
   response middleware'i `print("response sent")` basar. Bunu hem varsayılan kapsamla hem
   `scope="function"` ile çalıştır ve **sıralamayı** gözle. Mümkünse eski bir FastAPI sürümünde de dene.
3. Bu hatayı yakalayacak bir test tasarla: "201 aldım, hemen ardından okudum, kayıt yok" durumunu
   `TestClient` ile nasıl üretirsin? (İpucu: dependency çıkış kodunu yapay olarak geciktir.)
   `company/core-api/tests/` içine ekle.
4. `>=0.165,<1.0` neden hem alt hem üst sınır içeriyor? `==` ile sabitlemenin artı ve eksileri neler?

**İpuçları.**
- Hangi sürümde ne olduğunu **sürüm notlarından** doğrula; bu rehberdeki sürüm numaraları sadece hatırlatmadır.
- Değişikliğin `company/core-api/tests` ve `company/chaos/tests`'i bozmadığını kontrol et.

**Kabul kriterleri.**
- Deney çıktısı (sıra) PR açıklamasında; yeni test düzeltme geri alınınca kırılıyor.
- Core-api suite'i yeşil; sürüm aralığı gerekçesi yazılı.
- `tests/architecture` yeşil.

---

## 11. Yeni bir chaos senaryosu + eval vakası (Zor)

**Amaç.** Uçtan uca bir özellik eklemek: arıza enjeksiyonu, teşhis ve değerlendirme zinciri.

**Nereden başla.** `company/chaos/chaos/cli.py`, `company/chaos/chaos/scenarios/` (bir senaryo:
`stuck_provisioning.py`), `company/chaos/tests/`, `assistant/modes/diagnostic/checklist.py`,
`evals/scenario_suite.py` (`run_scenario_suite` ve `_FRESH_INJECTION_IDS`),
`evals/datasets/scenarios.yaml`, `evals/README.md` ("Adding a new scenario").

**Örnek senaryo.** `modem_offline`: bir müşterinin modemi çevrimdışı görünüyor (`diag.modem_status`
view'u bunu yansıtıyor; `get_modem_status` tool'u var). Beklenen: bu bir **bilinen kalıp mı**
(checklist'e yeni bir adım) yoksa **investigation**'a mı bırakılmalı? Kararını ADR tarzı bir
paragrafla gerekçelendir.

**İpuçları.**
- Chaos tarafında `company/` kodu asistan kelimelerini içeremez (sınır testi). Senaryo kodunda ve
  mesajlarında "assistant", "llm", "mcp" yazma.
- `reset` komutu yeni senaryoyu da geri almalı; `status` onu listelemeli.
- Eval tarafında: yeni id için `run_scenario_suite`'e bir enjeksiyon stratejisi bağla (üçünden birini
  seç), yoksa vaka dürüstçe `ERROR` olur.
- Beklentiler: doğru `issue_type`, aksiyon çalıştı mı / ticket var mı, departman.
- Karar checklist'e girerse `Diagnosis` için yeni bir `RootCause` üyesi, `ROOT_CAUSE_ACTION` ve
  `policy.yaml` gerekebilir; hepsi için test yaz.

**Kabul kriterleri.**
- `chaos` CLI'dan senaryo çalışıyor, `status` ve `reset` doğru.
- Chaos testleri (`company/chaos/tests`) ve yeni eval vakası PASS; `tests/architecture` yeşil.
- Tasarım kararının yazılı gerekçesi.

---

## 12. Sahte streaming yerine gerçek token streaming (Zor)

**Amaç.** Gerçek zamanlı akış ile güvenlik garantilerinin (maskeleme, doğrulama) nasıl çatıştığını
tasarlamak.

**Nereden başla.** `assistant/api/routes/chat.py` (dosya başlığındaki "Demo simplification"),
`assistant/web/static/widget.js` (zaten `token` ve `final` event'lerini bekliyor),
`assistant/llm/base.py` (`LLMProvider`), `assistant/llm/openai_agents.py`,
`assistant/llm/guard.py`, `assistant/privacy/masking.py`.

**Tartışılacak (önce yaz, sonra kodla).**
- Bugün cevap **tamamen** üretilip sonra bölünüyor. Hangi garantiler buna dayanıyor?
  (1) Maskeleme: bir telefon numarası iki token'a bölünürse regex görmez. (2) Advisory anlatımı
  `validate_model_reply` ile **tam metin** üzerinde doğrulanıyor; yarım metni doğrulayamazsın.
  (3) Investigation cevabı `finalize_finding` ve `mask_text` ile geçiyor.
  (4) Karar çağrıları (niyet, triage) zaten yapılandırılmış çıktı; akışa uygun değil.
- Seçenekler: (a) sadece **güvenli** metinleri (sabit şablonlar) gerçek akıt; (b) model cevabını
  arabelleğe al, kayan pencere ile maskele ve cümle sınırlarında yayınla; (c) iki aşamalı:
  önce "typing" göstergesi, sonra doğrulanmış metin. Her birinin gecikme/güvenlik takasını yaz.
- Widget bir `final` event'inde balonu `reply_en` ile **üzerine yazıyor** (`widget.js` içindeki
  `streamed.bubble.textContent = data.reply_en`): bu, akış sırasında bir sızıntı olsa bile
  nihai metnin doğru olduğunu garanti eder ama **sızıntıyı geri almaz**. Neden önemli?

**Kabul kriterleri.**
- Yazılı tasarım (1 sayfa) ve en az bir seçeneğin bayrak arkasında prototipi.
- Sınır testi: telefon/e-posta bir chunk sınırına denk geldiğinde ham PII istemciye **gitmiyor**.
- Mevcut `assistant/api/tests` yeşil; widget'ın POST yedeği hâlâ çalışıyor.

---

## 13. Açık investigation sırasında konu değişimi (Zor)

**Amaç.** Bilinen bir sınırı (bkz. `07-bilinen-sinirlar.md`) gerçek bir tasarım problemi olarak çözmek.

**Sorun.** Müşteri "internet yavaş" ile bir investigation başlatır, asistan bir soru sorar; müşteri
cevap yerine "aslında faturam yüksek geldi" yazar. `modes/dispatch.py` açık bir investigation
varken (`state.has_open_investigation()`) **router'ı atlar** ve önceki checklist sonucunu
yeniden kullanır; yani yeni konu eski bağlamda yorumlanır.

**Nereden başla.** `assistant/modes/dispatch.py` (satır ~101-118), `assistant/modes/state.py`,
`assistant/modes/investigation/prompts.py`, `assistant/modes/tests/test_investigation.py`
(`test_follow_up_turn_reuses_the_checklist_diagnosis`).

**İpuçları.**
- Tespit seçenekleri: (a) her mesajda ucuz bir "bu önceki sorunun cevabı mı?" sınıflandırması
  (LLM, güven eşiği ile); (b) anahtar kelime sezgisi; (c) investigation modeline "konu değişti"
  çıktısı eklemek. Her birinin maliyetini (tool bütçesi, gecikme, yanlış pozitif) yaz.
- Konu değiştiğinde `state.clear_investigation()` ve router'a geri dönüş; `ALLOWED_TRANSITIONS`'da
  `DIAGNOSTIC -> ROUTER` yok. Bunu eklemek mi, yoksa `DIAGNOSTIC -> CLOSING -> ROUTER` mi? State
  machine'in tablosunu değiştirmenin ne anlama geldiğini tartış.
- Güven eşiği altında ne yapılmalı? (Tercih: müşteriye sor, tahmin etme.)

**Kabul kriterleri.**
- Konu değişimi için testler (değişim var / yok / belirsiz).
- Mevcut investigation testleri yeşil; `tests/architecture` yeşil.
- Seçilen yaklaşımın gerekçesi ve reddedilenlerin listesi.
