# 03. Kavramlar Sözlüğü

Alfabetik (İngilizce terime göre). Her maddede üç şey var: **Kısa tanım**, **Projede nerede**,
**Neden önemli**. Dosya yolları repo köküne göredir.

İçindekiler: [A](#a) · [C](#c) · [D](#d) · [E](#e) · [F](#f) · [G](#g) · [H](#h) · [I](#i) ·
[L](#l) · [M](#m) · [P](#p) · [R](#r) · [S](#s) · [T](#t) · [Y](#y)

---

## A

### Adapter / anti-corruption layer
- **Kısa tanım:** İki sistemin arasına konan çevirmen katman. Bir tarafın iç modelinin ve
  dilinin diğer tarafa sızmasını (yani onu "bozmasını") engeller. Adapter pattern'in ağ sınırında
  uygulanmış hâli.
- **Projede nerede:** `integrations/mcp_*/`. Örneğin `integrations/mcp_core/app/tools_read.py`
  şirketin `diag.*` view'larını ve REST API'sini asistanın anlayacağı **tool**'lara çevirir.
  Asistan şirketin tablo adlarını bilmez; sadece tool adlarını ve `ToolResult` zarfını bilir.
- **Neden önemli:** Müşteri değiştiğinde değişen tek kod burası olur. Şirket şemasını yeniden
  düzenlese bile (view'lar sabit bir sözleşme olduğu sürece) asistan etkilenmez.

### Advisory lock
- **Kısa tanım:** PostgreSQL'in, tablo satırına bağlı olmayan, uygulamanın seçtiği bir
  **sayı anahtarı** üzerinden verdiği kilit. `pg_advisory_xact_lock(anahtar)` transaction bitince
  (commit/rollback) kilidi otomatik bırakır.
- **Projede nerede:** `assistant/audit/log.py`, `AuditLog._serialised_session` ve
  `_AUDIT_APPEND_LOCK_KEY`. Postgres dışında (SQLite, birim testleri) süreç içi bir `threading.Lock` kullanılır.
- **Neden önemli:** Audit zincirinde her satır bir öncekinin hash'ine bağlı. İki yazıcı aynı anda
  "son satır budur" diye okursa **aynı öncülden iki dal** çıkar ve zincir çatallanır. Kilit,
  "öncülü oku -> hash hesapla -> ekle -> commit" dizisini tek kişiye serileştirir. Bedeli:
  appendler sıraya girer (bkz. `04-tasarim-kararlari.md`).

### Append-only + DB trigger
- **Kısa tanım:** Sadece eklemeye izin veren tablo. Burada kural uygulama kodunda değil,
  **veritabanının kendisinde**: `UPDATE` ve `DELETE` bir trigger ile reddedilir.
- **Projede nerede:** `assistant/core_common/db.py`, `AUDIT_APPEND_ONLY_TRIGGER_SQL`
  (`BEFORE UPDATE OR DELETE ... RAISE EXCEPTION`). Test: `tests/integration/test_audit_append_only.py`.
- **Neden önemli:** Uygulama kodu hatalı veya ele geçirilmiş olabilir; veritabanı katmanındaki
  kural onun *altında* kalır. Denetim kaydı için doğru güvence seviyesi budur.

### Audit hash chain
- **Kısa tanım:** Her kayıt, bir önceki kaydın hash'ini de içeren bir hash taşır
  (`entry_hash = sha256(prev_hash + canonical_json(kayıt))`). Geçmişte bir satırı değiştirirsen
  ondan sonraki **tüm** hash'ler tutmaz.
- **Projede nerede:** `assistant/audit/log.py` (`compute_entry_hash`, `AuditLog.verify_chain`);
  testler `assistant/audit/tests/test_hash_chain.py`.
- **Neden önemli:** Append-only trigger'ı "kötü niyetli tek bir UPDATE"i engeller; hash zinciri ise
  veritabanına doğrudan erişimi olan biri bile bir şeyi sessizce değiştirdiyse bunun **fark
  edilmesini** sağlar. İkisi birbirini tamamlar: biri önler, diğeri kanıtlar. Her konuşmanın
  kendi zinciri var (`conversation_id` başına), `seq` ise global.

---

## C

### Chaos engineering
- **Kısa tanım:** Sisteme bilerek ve kontrollü şekilde arıza enjekte edip davranışını gözlemek.
- **Projede nerede:** `company/chaos/` (CLI: `python -m chaos.cli <senaryo>`). Senaryolar
  `company/chaos/chaos/scenarios/`: `stuck_provisioning`, `paid_not_active`, `regional_outage`,
  `double_charge`, `missed_installation`, `payment_down`. `reset` hepsini geri alır.
- **Neden önemli:** "Asistan arızayı doğru teşhis eder mi?" sorusu sadece gerçekçi arıza verisiyle
  sınanabilir. Chaos senaryoları hem demo hem de eval verisinin kaynağı.

### Composition root
- **Kısa tanım:** Uygulamada somut nesnelerin yaratılıp birbirine bağlandığı **tek** yer. Diğer
  tüm kod bağımlılıklarını hazır alır, kendisi yaratmaz.
- **Projede nerede:** `assistant/api/state.py` (`startup()`, modül düzeyindeki `state`).
  Orchestrator'ın per-turn bağlamı için `assistant/modes/orchestrator.py::build_ctx`.
- **Neden önemli:** Sağlayıcıyı veya gateway'i değiştirmek rota kodunu etkilemez; testte
  `state` alanlarını doğrudan değiştirerek sahte nesneler koyabilirsin.
  (Not: `state` burada kolaylık için global bir singleton. Büyük uygulamalarda FastAPI
  `Depends` ve `app.state` tercih edilir; dosya başlığında da böyle yazıyor.)

### Confidence floor (güven eşiği)
- **Kısa tanım:** Modelin "ne kadar eminim" skoru belirli bir eşiğin altındaysa cevabına
  güvenmeyip güvenli bir alternatife (insan, sabit tablo) düşme kuralı.
- **Projede nerede:** `assistant/decision/fallback.py::apply_confidence_floor`; eşik
  `config/tenants/<tenant>/policy.yaml` içinde `decision.min_confidence` (netswift: 0.6, `_example`: 0.8).
  Kullanım: `assistant/modes/router.py:77`, `assistant/modes/investigation/finding.py::finalize_finding`.
- **Neden önemli:** LLM'ler yanlış ama kendinden emin olabilir; ama düşük güvenle konuşuyorsa
  bunu sinyal olarak kullanabiliriz. Eşiğin altında **tahmin etmek yerine** insana devrederiz.
  Aciliyet (urgency) kararlarında ek olarak "asla aşağı çekme" kuralı var (`urgency_floor`).

---

## D

### Default-deny
- **Kısa tanım:** Açıkça izin verilmeyen her şey yasak. İzin listesi mantığı.
- **Projede nerede:** `assistant/policy/engine.py::PolicyEngine.check`. `policy.yaml` içinde
  olmayan bir aksiyon `action_not_in_policy` ile reddedilir.
- **Neden önemli:** Yeni bir aksiyon eklerken "unutulan" durum güvenli tarafta kalır. Tersi
  (default-allow) olsaydı her unutulan kontrol bir güvenlik açığı olurdu.

### Dependency inversion (provider seam)
- **Kısa tanım:** Üst seviye kodun somut sınıfa değil bir **arayüze** (Protocol) bağımlı olması;
  somut uygulamanın dışarıdan verilmesi.
- **Projede nerede:** `assistant/llm/base.py` içindeki `LLMProvider` Protocol'ü. OpenAI
  (`openai_agents.py`), Anthropic (`anthropic_provider.py`) ve test için `scripted.py` aynı arayüzü
  uygular; `assistant/llm/factory.py::get_provider` `LLM_PROVIDER` ayarına göre seçer ve
  `MaskingProvider` ile sarar. Benzer bir "dikiş yeri" (seam): `assistant/decision/base.py`
  (`DecisionService`).
- **Neden önemli:** Testler gerçek modele ihtiyaç duymaz; yeni bir sağlayıcı eklemek sadece yeni
  bir sınıf demektir. "Seam" (dikiş yeri), davranışı kodu değiştirmeden değiştirebildiğin noktadır.

### Deterministic vs. LLM decision
- **Kısa tanım:** Aynı girdiye her zaman aynı çıktıyı veren kod ile olasılıksal model çıktısı
  arasındaki fark ve hangisini nerede kullanacağını seçme sanatı.
- **Projede nerede:** Deterministik: `assistant/recommendation/engine.py` (paket puanlama),
  `assistant/modes/diagnostic/checklist.py`, `assistant/modes/action/known_causes.py::ROOT_CAUSE_ACTION`,
  `assistant/policy/engine.py`. LLM: niyet sınıflandırma, issue type/öncelik/departman (güven
  eşiği ve deterministik yedeklerle), investigation, öneri anlatımı.
- **Neden önemli:** Para, yetki ve öneri sıralaması gibi yerlerde **tekrarlanabilirlik** ve
  denetlenebilirlik şart. LLM'i, belirsizliğin doğal olduğu yerlere (serbest metni anlamak)
  sıkıştırıyoruz.

---

## E

### Enum validation of model output
- **Kısa tanım:** Modelin döndürdüğü etiketi, kabul edilebilir değerlerin kapalı listesine
  (enum) karşı doğrulamak. Liste dışı = güvenilmez.
- **Projede nerede:** `assistant/decision/llm_structured.py`. Model `value` alanını düz string
  döner, sonra `Intent`/`Department`/`Priority`/`IssueType` enum'una çevrilir; geçersizse
  güven 0'lı güvenli bir `Decision` olur. Benzer: `modes/investigation/finding.py`
  (`FindingOutcome`, `resolve_department`).
- **Neden önemli:** Modelin uydurduğu bir departman adı bir ticket'ı yanlış kuyruğa atabilir.
  "Modelin çıktısı girdidir, komut değildir" ilkesi.

### Evals (scripted vs. live)
- **Kısa tanım:** Bir AI sisteminin davranışını önceden tanımlı vakalarla ölçen değerlendirme
  düzeneği. Unit test'ten farkı: tek bir fonksiyonu değil **uçtan uca davranışı** (doğru mode,
  doğru teşhis, doğru departman, gereksiz ticket yok) ölçer.
- **Projede nerede:** `evals/` (`python -m evals.run`), veri `evals/datasets/*.yaml`. `EVAL_MODE=scripted`
  (varsayılan; `ScriptedProvider`, API anahtarı gerekmez) veya `live` (gerçek model).
- **Neden önemli:** Scripted modda cevaplar önceden yazıldığı için **modeli değil, tesisatı**
  (state machine, policy, gateway, audit) ölçersin. Gerçek model kalitesi için `live` mod gerekir.
  Ayrıntı: `05-test-ve-degerlendirme.md`.

---

## F

### Fail-safe defaults
- **Kısa tanım:** Bir şey ters gittiğinde sistemin **güvenli** tarafa düşmesi. Belirsizlik = en
  kısıtlı davranış.
- **Projede nerede:** `assistant/policy/engine.py`: koşulun verisi yoksa koşul "sağlanmadı" sayılır.
  `assistant/api/security.py::verified_json`: `WEBHOOK_SECRET` tanımsızsa istek **reddedilir**
  ("fail closed"). `assistant/llm/guard.py`: ham PII görürse sağlayıcıyı hiç çağırmadan durur.
  `modes/investigation/finding.py`: düşük güvenli bulgu insana devredilir.
- **Neden önemli:** Güvenlikte varsayılan hatalar "izin ver" yönünde olursa saldırgan için
  davettir. Burada her belirsizlik bir devir veya reddetmedir.

---

## G

### Graceful degradation (zarif bozulma)
- **Kısa tanım:** Bir bileşen çöktüğünde tüm sistemin düşmemesi; kalan özelliklerle ve açık bir
  mesajla çalışmaya devam etmesi.
- **Projede nerede:** `assistant/api/state.py::startup` DB, tenant config veya LLM sağlayıcı
  kurulamazsa çökmez, `init_error_en` kaydeder; `/health` yeşil kalır, `/api/chat` açıklayıcı bir
  cevap verir (`degraded_reply`). `assistant/mcp_gateway/gateway.py`: adapter ulaşılamazsa
  `ok=False, error_code="ADAPTER_UNAVAILABLE"` döner, exception değil. Langfuse kapalıysa
  trace'ler yerel JSONL dosyasına düşer (`assistant/observability/fallback.py`).
- **Neden önemli:** Zorunlu olmayan bir bağımlılık (izleme, monitoring adapter'ı) yüzünden müşteri
  sohbeti kesilmemeli.

---

## H

### HMAC webhook imzası + constant-time karşılaştırma
- **Kısa tanım:** Webhook'u gönderenin gerçekten gizli anahtarı bildiğini kanıtlamak için isteğin
  **ham gövdesi** üzerinde hesaplanan `HMAC-SHA256` imzası. Karşılaştırma `hmac.compare_digest` ile,
  **sabit sürede** yapılır ki cevabın süresi imzanın kaç karakterinin doğru olduğunu sızdırmasın
  (timing attack).
- **Projede nerede:** `assistant/api/security.py::verify_webhook_signature` ve `verified_json`
  (başlık: `X-Webhook-Signature: sha256=<hex>`); gönderen tarafın karşılığı `company/shared/auth.py`.
  İki taraf algoritmada **anlaşır**, kod paylaşmaz (sınır testi buna izin vermez). Kullanım:
  `assistant/api/routes/webhooks.py`.
- **Neden önemli:** Webhook bir endpoint'tir; imza yoksa internetteki herkes "ticket kapandı"
  veya "alarm çaldı" diye asistanı tetikleyebilir. İmzayı hesaplamak için gövdeyi parse etmeden
  **ham bayt** olarak okumalısın; yoksa JSON'u yeniden serileştirince bayt dizisi değişir.

---

## I

### Idempotency key
- **Kısa tanım:** Aynı isteğin tekrarının aynı sonucu vermesini sağlayan istemci tarafı anahtar.
  İstek ağ hatası yüzünden tekrar edilirse çift işlem olmaz.
- **Projede nerede:** `company/payment-gateway-mock/app/engine.py::create_charge` aynı
  `idempotency_key` ile gelen isteği yeniden işlemez, var olan ücreti döner; veritabanı tarafında
  benzersiz sütun (`models.py`). `double_charge` chaos senaryosu (`company/chaos/chaos/scenarios/double_charge.py`)
  veritabanına yeni bir anahtarla ikinci bir kayıt ekleyerek bilerek çift çekim **simüle eder**.
  Entegrasyon katmanında `integrations/mcp_core/app/tools_write.py::apply_outage_credit` de bir
  idempotency key gönderir (dosya başlığına bakın), yani yeniden denenen çağrı müşteriyi iki kez
  kredilendirmez.
- **Neden önemli:** Ödeme gibi yan etkili işlemlerde "yeniden dene" güvenli olmalı. Asistanın
  `detect_duplicate_charges` ile aradığı "çift çekim" de, bu korumanın bir şekilde devre dışı kaldığı (ya da hiç olmadığı) durumların sonucudur.

---

## L

### LLM structured output
- **Kısa tanım:** Modelden serbest metin yerine, önceden tanımlı bir **şemaya** (pydantic modeli /
  JSON schema) uyan nesne istemek.
- **Projede nerede:** `assistant/llm/base.py::LLMProvider.structured`; `assistant/decision/llm_structured.py`
  her karar için `{value, confidence, rationale}` şemasını kullanır. Investigation'ın sonucu da
  `InvestigationFinding` şemasına uyan bir `submit_finding` tool çağrısıdır
  (`modes/investigation/finding.py`).
- **Neden önemli:** Kod, "cevabı metinden ayıklamaya" çalışmaz; doğrulanabilir bir nesne alır.
  Yine de bu, içeriğin doğru olduğu anlamına gelmez; bu yüzden enum doğrulaması ve güven eşiği var.

---

## M

### MCP (Model Context Protocol)
- **Kısa tanım:** Yapay zekâ uygulamalarının dış sistemlere "tool" olarak bağlanması için açık
  protokol. Sunucu tool'ları listeler (`list_tools`) ve çağırır (`call_tool`); istemci (bu
  projede asistan) bunları keşfedip kullanır.
- **Projede nerede:** Sunucular `integrations/mcp_*/` (streamable HTTP, `/mcp` yolu); istemci
  `assistant/mcp_gateway/gateway.py`. Adreslerin kaynağı `config/tenants/<tenant>/tenant.yaml`.
- **Neden önemli:** Asistan ile şirket sistemleri arasında standart, dil-bağımsız ve keşfedilebilir
  bir sözleşme sağlar. Not: `assistant/mcp_gateway/` paketinin adı `mcp` olamaz, çünkü kurulu MCP
  SDK'sını gölgelerdi.

### Multi-tenancy (çoklu kiracı)
- **Kısa tanım:** Aynı yazılımın birden fazla müşteri (tenant) için, **sadece konfigürasyonla**
  farklı çalışması.
- **Projede nerede:** `config/tenants/netswift/` ve `config/tenants/_example/`; yükleyici
  `assistant/core_common/config.py::load_tenant_config` (`TENANT` ortam değişkeni).
- **Neden önemli:** İkinci müşteri geldiğinde kod değil config yazarsın. Bu projede bir süreç bir
  tenant ile çalışır; istek başına tenant seçimi yok (bkz. `07-bilinen-sinirlar.md`).

---

## P

### PII masking / KVKK-GDPR
- **Kısa tanım:** Kişisel verinin (PII: ad, telefon, e-posta, adres, kimlik numarası, IBAN, kart
  numarası) üçüncü taraflara gitmeden önce yer tutucu ile değiştirilmesi. Projedeki kural
  **UK GDPR** içindir; Türkiye'deki karşılığı **KVKK**'dır ve ilke aynıdır (veri minimizasyonu).
- **Projede nerede:** `assistant/privacy/masking.py` (`mask_text`, `mask_payload`, `assert_no_pii`)
  ve `assistant/privacy/detectors.py` (regex + Luhn). Üç savunma hattı: girişte maskeleme
  (`orchestrator.py:96`), `llm/guard.py::MaskingProvider` (model çağrısından hemen önce),
  `audit/log.py` (`assert_no_pii`). İzin verilen kimlikler (`customer_no`, `ticket_key`…)
  `policy.yaml: pii.allow_unmasked` listesinde.
- **Neden önemli:** Veri bir kez bir model sağlayıcıya veya log'a gittiyse geri alınamaz.
  Dedektörler **dar** tutulmuş: yanlış şeyi maskelemektense bir kenar durumu kaçırmak tercih edilir.
  Kapsam İngiltere formatlarıyla sınırlıdır: Türkçe dedektörler için `06-alistirmalar.md`.

### Policy engine
- **Kısa tanım:** "Bu aksiyon şimdi yapılabilir mi?" sorusunu **veriyle** (yaml kuralları) cevaplayan
  motor. Modelin kararından bağımsızdır.
- **Projede nerede:** `assistant/policy/engine.py` (karar), `assistant/policy/executor.py`
  (çalıştırma), `config/tenants/netswift/policy.yaml` (kurallar). Değerlendirme sırası ve ilk
  hatada durma: bilinmeyen aksiyon -> `allowed: false` -> koşullar -> rate limit -> tutar sınırı;
  hepsi geçerse `requires_confirmation` varsa onay istenir.
- **Neden önemli:** Prompt injection ile model "iade yap" demeye ikna edilebilir; ama yetki bir
  prompt'ta değil kodda ve config'te olduğundan bu aksiyon hiç çalışmaz.

---

## R

### Read-only DB role
- **Kısa tanım:** Veritabanında sadece okuma yetkisi olan kullanıcı. Yazma engeli kodun dürüstlüğüne
  değil veritabanının kendisine dayanır.
- **Projede nerede:** `readonly_diag` rolü yalnızca `diag.*` view'larında `SELECT` yetkisine sahip;
  `core.*` kapalı (`company/core-api/app/bootstrap.py::_grant_statements`).
  `integrations/common/readonly_db.py` ek olarak `READ ONLY` transaction açar ve `SELECT/WITH`
  dışı ifadeleri reddeder. Test: `tests/integration/test_readonly_role.py`.
- **Neden önemli:** "Entegrasyon katmanı yazamaz" iddiası, en güçlü hâliyle **yapısal olarak
  imkânsız** olmasıdır. View'lar ayrıca PII sütunu içermez; hassas veri hiç görünmez.

---

## S

### SSE (Server-Sent Events)
- **Kısa tanım:** Sunucudan tarayıcıya tek yönlü, uzun ömürlü bir HTTP cevabı; içinde
  `event:` / `data:` çerçeveleri akar.
- **Projede nerede:** Sunucu `assistant/api/routes/chat.py::api_chat_stream` (`sse_starlette`),
  istemci `assistant/web/static/widget.js` (`EventSource`). Hata olursa widget aynı turu
  `POST /api/chat` ile yeniden dener.
- **Neden önemli:** WebSocket'e göre daha basit bir seçim. Not: bu projedeki akış **gerçek token
  streaming değil**, hazır cevabı kelimelere böler (bkz. `07-bilinen-sinirlar.md`).

### State machine (durum makinesi)
- **Kısa tanım:** Sistemin sonlu sayıda durumdan birinde olması ve geçişlerin açık bir tabloyla
  tanımlanması.
- **Projede nerede:** `assistant/modes/machine.py` (`ALLOWED_TRANSITIONS`, satır 30;
  `StateMachine.transition`). Durumlar: `Mode` enum'u (`assistant/core_common/types.py`).
  Yürütücü: `assistant/modes/dispatch.py`.
- **Neden önemli:** Tüm konuşma akışını tek ekranda okuyabilirsin. Handler'lar mode'u kendileri
  değiştirmez; geçişi makine onaylar, geçersiz geçiş `IllegalTransition` ile **yüksek sesle** patlar.
  `StateMachine` yalnızca `max_actions_per_conversation` için `check_actions_per_conversation`
  sunar (`LimitExceeded`); `ActionExecutor` bunu bir aksiyon çalışmadan (ve onay istemeden) önce
  çağırır. Diğer iki limit sayacın yaşadığı yerde uygulanır: tool bütçesi gateway'de
  (`ToolBudgetExceeded`), advisory soru sınırı `advisory/flow.py`'de. Aynı limit iki yerde
  tekrarlanmadığı için ölü kod kalmadı (bkz. `07-bilinen-sinirlar.md`).

---

## T

### Tool budget (tool bütçesi)
- **Kısa tanım:** Bir turda yapılabilecek tool çağrısı sayısının üst sınırı.
- **Projede nerede:** `assistant/mcp_gateway/gateway.py` (`for_turn`, `reset_turn_budget`, N+1.
  çağrıda `ToolBudgetExceeded`); limit `policy.yaml: limits.max_tool_calls_per_turn` (netswift: 14).
  Orchestrator aşımı yakalayıp kibarca insana devreder (`modes/orchestrator.py:124`).
  `TurnContext.call_tool_cached` aynı okumayı tur içinde bir kez harcar.
- **Neden önemli:** Başıboş bir model döngüsü şirket sistemlerini dövemez; maliyet ve gecikme
  sınırlı kalır. Her tur kendi gateway görünümünü ve kendi bütçesini alır (`for_turn`), bu yüzden bir konuşma başkasının hakkını yiyemez.

### Tool calling / agent loop
- **Kısa tanım:** Modele tool'ların tanımını verip, modelin "şu tool'u şu argümanlarla çağır"
  demesi; kodun çağırıp sonucu modele geri vermesi; bunun bir **bitiş koşuluna** kadar tekrarı.
- **Projede nerede:** `assistant/modes/investigation/loop.py::run_investigation`
  (provider arayüzü: `LLMProvider.run_tool_step`, `assistant/llm/base.py`). Döngü sınırlı,
  allowlist'li, salt-okunur ve `submit_finding` ile biter.
- **Neden önemli:** Ajan davranışının özü budur. Güvenli kılan, modelin **ne yapabileceğini**
  kodun sınırlamasıdır: tool listesi, argüman kapsamı, adım sayısı, bitiş şeması.

### Typed state (pydantic)
- **Kısa tanım:** Durumu "gevşek bir dict" yerine tipli bir model olarak tutmak. Yanlış yazılmış
  bir alan adı sessiz `None` yerine hemen hata verir.
- **Projede nerede:** `assistant/modes/state.py::ConversationState` (pydantic). `extra="ignore"` ve
  varsayılan değerler sayesinde eski satırlar yeni sürümle de yüklenir (geriye uyumlu).
  Diğer örnekler: `Diagnosis`, `PackageOffer` (`assistant/core_common/types.py`).
- **Neden önemli:** Veritabanında JSON olarak saklanan bir alanın şeması zamanla evrilir; tip +
  varsayılan değerler bu evrimi güvenli hâle getirir.

---

## Y

### Yield dependency ve commit-before-response
- **Kısa tanım:** FastAPI'de `yield` kullanan bir dependency (`get_db` gibi) isteğin başında
  kaynağı verir, `yield`'den **sonraki** kod ise temizlik içindir. Bu "sonraki kod"un cevap
  istemciye gönderilmeden **önce** mi, **sonra** mı çalıştığı FastAPI sürümüne bağlıdır. Sürüm
  notlarına göre yeni sürümlerde (0.118 ve sonrası) yield sonrası kod cevap gönderildikten sonra
  çalışır; `Depends(get_db, scope="function")` (0.121+) ise dependency'nin kapsamını endpoint
  fonksiyonunun bitişine bağlayarak commit'in cevaptan **önce** olmasını sağlar. (Kendi
  kurulu sürümünün davranışını sürüm notlarından ve küçük bir deneyle doğrula.)
- **Projede nerede:** `company/core-api/app/db.py::get_db` (`yield session` -> `session.commit()`).
  Düzeltme **uygulandı**: router'lar `Depends(get_db, scope="function")` kullanıyor
  (`company/core-api/app/routers/`) ve `company/requirements.txt` `fastapi>=0.165,<1.0` ile
  sürümü bilinçli bir aralığa bağlıyor. `get_db` docstring'i dersi de anlatıyor. Bunu bir ders
  olarak oku ve alıştırma 10'daki gibi kendin doğrula: `06-alistirmalar.md`.
- **Neden önemli:** Cevap `200 OK` ile istemciye gitti ama commit henüz yapılmadıysa, istemci hemen
  ardından okuma yaptığında yazdığı veriyi **göremeyebilir**; ya da commit sonradan patlarsa istemci
  başarılı sanıp gitmiş olur. Bu, `ticket oluşturuldu` cevabı alıp ticket'ı bulamamak, ya da bir
  testin "yazdım, okuyorum, yok" diye bazen kırılması gibi **sinsi, zamanlamaya bağlı** hatalar
  üretir. Ders: kritik yan etkiyi (commit) cevabı göndermeden önce garanti et ve bağımlılık
  sürümlerini sabitle ki "yarın başka davranış" riski olmasın (bkz. `04-tasarim-kararlari.md`).
