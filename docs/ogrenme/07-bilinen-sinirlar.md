# 07. Bilinen Sınırlar ve Demo Sadeleştirmeleri

Bu projede bazı şeyler **bilerek** basit tutuldu, bazıları ise bu commit'te eksik. İyi bir mühendis
ikisini ayırt eder ve üretime geçerken ne yapılması gerektiğini bilir. Her madde için üç soru:
**Ne?**, **Burada neden kabul edilebilir?**, **Production'da ne yapılırdı?**

Bölüm A bilinçli demo sadeleştirmeleri (kodda `Demo simplification:` etiketiyle de bulabilirsin:
`grep -rn "Demo simplification" assistant`). Bölüm B kodu okurken fark ettiğimiz, dürüstçe
söylemek istediğimiz boşluklar. Bölüm C test/operasyon gözlemleri.

---

## A. Bilinçli demo sadeleştirmeleri

### A1. Parolasız demo girişi
- **Ne?** `POST /api/login` sadece müşteri numarasının formatını ve varlığını kontrol eder
  (`assistant/api/routes/auth.py`); parola yok. Numarayı bilen o müşteri olarak girebilir. Cookie
  `httponly` ve `samesite=lax` ama `secure` değil ve imzalı/süreli değil.
- **Neden kabul edilebilir?** Demo, "asistan kimliği cookie'den alır ve sahiplik kontrol eder"
  mimarisini göstermek için. Kimlik doğrulama başka bir ürünün (şirketin kimlik sağlayıcısı) işi.
- **Production'da?** Şirketin gerçek kimlik sağlayıcısı (OIDC/SSO), imzalı ve süresi dolan
  oturum, `secure=True` cookie, CSRF koruması, oturum iptali, brute-force/rate limit.

### A2. Sahte (fake) kelime akışı
- **Ne?** `GET /api/chat/stream` cevabı tamamen üretip sonra boşluklara göre kelime kelime `token`
  event'i yollar (`assistant/api/routes/chat.py`, "Demo simplification" notları).
- **Neden kabul edilebilir?** Widget'ın akış arayüzünü ve SSE sözleşmesini göstermeye yeter. Gerçek
  akış maskeleme ve doğrulama (policy, `validate_model_reply`) ile çatışır, çünkü bu kontroller tam
  metin ister. Önce güvenliği doğru kurduk.
- **Production'da?** Model token'larını akıtmak, ama akıtmadan önce arabellekle maskeleme/doğrulama
  (yalnızca güvenli yollarda akış) uygulamak. Alıştırma 12 tam bu problemi işliyor.

### A3. Bir süreç = bir tenant
- **Ne?** `TENANT` ortam değişkeni süreç açılırken bir kez okunur; istek başına tenant seçimi yok.
- **Neden kabul edilebilir?** Çoklu kiracılığın *konfigürasyonla* sağlanabildiğini kanıtlamak için
  yeterli (`config/tenants/_example/`).
- **Production'da?** İstek başına tenant çözümlemesi (alan adı, API anahtarı veya oturum),
  tenant bazlı DB şeması/izolasyonu, tenant başına kaynak kotaları.

### A4. Tek bir global audit kilidi
- **Ne?** Her audit yazımı tek bir advisory lock'tan geçer (`assistant/audit/log.py`).
- **Neden kabul edilebilir?** Demo hacminde sorun değil; zincir bütünlüğünü basitçe garanti eder.
- **Production'da?** Zinciri konuşma/tenant başına bölmek, ya da yazımı sıraya koyan tek bir
  yazıcı servis; yüksek hacim için Merkle ağacı veya imzalı toplu kayıt.

### A5. Çağrı başına yeni MCP oturumu
- **Ne?** `ToolGateway.call` her çağrıda yeni bir MCP bağlantısı ve oturumu açar
  (`assistant/mcp_gateway/gateway.py`: `async with streamable_http_client(...)`,
  `session.initialize()`); `call_sync` bunu her seferinde `asyncio.run` ile sarar.
- **Neden kabul edilebilir?** Basit, durumsuz, kaynak sızıntısı yok. Bir turda ~7-14 çağrı olunca
  ek maliyet kabul edilebilir.
- **Production'da?** Uzun ömürlü oturum havuzu ve yeniden bağlanma, paralel okuma çağrıları
  (bağımsız checklist adımları aynı anda), bağlantı sağlık kontrolleri.

### A6. İlk açılışta tool kataloğunun keşfi ve tek süreçli durum
- **Ne?** `state` modül düzeyinde bir singleton (`assistant/api/state.py`); kataloğu paylaşan kök
  gateway startup'ta kurulur.
- **Neden kabul edilebilir?** Okunması ve testte override edilmesi kolay; dosya başlığı bunu
  açıkça "teaching simplification" olarak belirtiyor.
- **Production'da?** `app.state` + FastAPI `Depends` ile bağımlılık enjeksiyonu; birden fazla
  worker'da paylaşılan durumun (kataloglar, önbellekler) bilinçli yönetimi.

---

## B. Kodu okurken fark ettiğimiz sınırlar

### B1. Sadece İngiltere'ye özgü PII maskeleme
- **Ne?** Dedektörler UK National Insurance numarası, UK mobil telefon (`+44 7...`/`07...`),
  `GB` IBAN, e-posta ve Luhn doğrulamalı kart numarasını tanır (`assistant/privacy/detectors.py`).
  T.C. kimlik no, `+90` telefon, `TR` IBAN gibi formatlar **maskelenmez**.
- **Neden kabul edilebilir?** Hayali bir İngiliz şirket için yazıldı; dedektörler bilerek "dar"
  (yanlış şeyi maskelemektense kaçırmayı tercih eder).
- **Production'da?** Hedef ülkelere göre dedektör kütüphanesi, birim testli doğrulama algoritmaları,
  bir PII tarama aracıyla (ör. DLP) çift savunma, ve veri sınıflandırması. Türkiye için KVKK
  kapsamında bu eklenmeli: Alıştırma 6.

### B2. Sadece İngilizce cevaplar
- **Ne?** Müşteriye gösterilen sabit metinler `assistant/modes/replies.py` içinde İngilizce;
  model cevapları ve ticket'lar da İngilizce yazılıyor; tenant `locale` alanı bugün çeviri yapmıyor.
- **Neden kabul edilebilir?** Bir dil, tek bir demo. `replies.py` zaten çeviri için bir "dikiş yeri"
  olarak tasarlandı.
- **Production'da?** Locale tabanlı mesaj kataloğu (ICU/gettext), modeli hedef dilde yazmaya yönlendiren
  prompt'lar, dil algılama, çeviri kalite kontrolü. Departman ticket'ları için ayrı dil kararı.
  Alıştırma 7.

### B3. Anthropic tool yolu yalnızca mock ile test edildi
- **Ne?** `LLM_PROVIDER=anthropic` seçeneği mevcut, ancak `run_tool_step` yolu bu repoda yalnızca
  sahte (mock) istemciyle test edildi (`assistant/llm/tests/test_tool_step.py`). Varsayılan sağlayıcı
  OpenAI'dır; `EVAL_MODE=live` de bu repoda koşulmadı (`evals/README.md`).
- **Neden kabul edilebilir?** Provider seam'in (arayüz) çalıştığını kanıtlıyor; ikinci sağlayıcı
  eklemek mimari olarak ucuz.
- **Production'da?** Her desteklenen sağlayıcı için gerçek API'ye karşı düzenli entegrasyon/eval
  koşusu (maliyet bütçesiyle), sağlayıcı davranış farklarının (tool çağrı biçimi, hata kodları)
  testleri.

### B4. Açık investigation sırasında konu değişimi
- **Ne?** Bir investigation soru sormuşsa konuşma `DIAGNOSTIC`'te kalır; sonraki mesaj router'dan
  geçmez ve önceki checklist sonucu yeniden kullanılır (`assistant/modes/dispatch.py`, satır
  101-110). Müşteri konu değiştirirse yeni konu eski bağlamda yorumlanır.
- **Neden kabul edilebilir?** Çoğu takip mesajı gerçekten cevaptır; yeniden sınıflandırmak her tura
  bir model çağrısı ve yanlış pozitif riski ekler.
- **Production'da?** Her takip mesajında "cevap mı, yeni konu mu?" tespiti (güven eşiğiyle) ve
  konu değişince state'i temizleyip router'a dönmek. Alıştırma 13.

### B5. Bazı `policy.yaml` limitleri ve koşulları bugün tam uygulanmıyor
Kodu okurken şunları gördük (yeni bir geliştirici olarak bunları bilmek seni yanlış varsayımdan korur):
- `limits.max_actions_per_conversation` (netswift: 3) config'te ve `StateMachine.check_actions_per_conversation`
  içinde var, ancak bu commit'te **üretim kodundan çağrılmıyor**; yalnızca `modes/tests/test_machine.py`
  çağırıyor. Aynısı `check_tool_calls` ve `check_questions_advisory` için de geçerli: o limitler
  başka yollarla uygulanıyor (tool bütçesi `ToolGateway`'de; advisory soru sınırı `advisory/flow.py`'de
  doğrudan config'ten okunuyor).
- `build_policy_context` (`assistant/modes/action/known_causes.py:60`) `credit.existing_count_30d`
  değerini sabit `0` ve `amount_gbp` değerini sabit `5.0` olarak veriyor. Yani `apply_outage_credit`
  için "son 30 günde kredi almamış olmalı" koşulu bugün **her zaman sağlanıyor**; gerçek koruma
  şirketin kendi API'sinde olmalı (mcp-core tarafı idempotency key gönderir).
- `policy.yaml: rate_limit` sayaçları `context["usage"]` içinden okunur, ama üretim kodu bu anahtarı
  doldurmuyor; bu yüzden hız sınırları bugün hiç tetiklenmez (policy testleri `usage`'ı elle verir).
- `config/tenants/_example` `reschedule_installation` aksiyonuna onayla izin veriyor, ancak
  `action_runner.ACTION_TOOL_MAP` içinde bu aksiyonun karşılığı yok (onaydan sonra
  `UnmappedActionError`), ayrıca mcp-core'daki ilgili tool partner anahtarının yetkisi olmadığı için
  her zaman `SCOPE_DENIED` döner (`integrations/mcp_core/app/tools_write.py` başlığı). Yani o
  müşterinin "randevu erteleyebilir" yeteneği henüz uçtan uca çalışmıyor. Alıştırma 8 buna götürür.
- `routing.yaml: diagnostic_checklist` listesi config modelinde doğrulanıyor ama checklist'in
  adım sırası `modes/diagnostic/checklist.py` içinde kodla sabit.
- **Neden kabul edilebilir?** Demo akışları bunlara dokunmuyor; boşluklar test ve eval tarafından
  yakalanmadı, ki bu da kendi başına öğretici (Alıştırma 8).
- **Production'da?** Her config anahtarı için "okunuyor mu, uygulanıyor mu?" kontrolü: ölü config
  anahtarlarını bulan bir test, gerçek kullanım sayaçları (konuşma başına aksiyon sayısı, müşteri başına
  günlük kredi), ve config ile kod uyumunu doğrulayan testler.

### B6. Ek küçük gözlemler
- Birçok dosya başlığında bölüm işareti için `§` karakteri bozuk kodlanmış (`§`) görünüyor
  (örn. `assistant/policy/engine.py` başlığındaki "See: docs/contracts.md §4.6"). Bu bir
  karakter kodlama sorunu; içeriği etkilemez ama aramayı zorlaştırır.
- Bir iş turu için tool bütçesi 14 (`policy.yaml`); `.env.example` içinde `MAX_TOOL_CALLS_PER_TURN=8`
  tanımlı. Gateway'in kurucu varsayılanı da 8; ancak çalışma zamanında `from_tenant_config` politika
  değerini (14) kullanır. İki "limit"in nerede etkili olduğuna dikkat.

---

## C. Test ve operasyon gözlemleri

### C1. Flaky test etkileşimleri
- **Ne?** Suite'ler aynı `company-db`'yi paylaşıyor ve chaos senaryoları gerçek satırları değiştiriyor;
  ayrıca stack yeni kalktığında testler provisioning worker ile yarışabiliyor
  (`05-test-ve-degerlendirme.md`, bölüm 7).
- **Neden kabul edilebilir?** Geliştirici makinesinde tek stack; dokümante edilmiş ve bilinen bir kalıp.
- **Production'da?** Her test koşusu için izole veritabanı/şema (testcontainers veya geçici DB),
  deterministik zaman, worker'ın testlerde kontrol edilebilir olması, CI'da flaky takip raporu.

### C2. Tur başına ~7–9 saniye gecikme (gpt-4.1-mini ile)
- **Ne?** Gözlemlenen: gpt-4.1-mini ile bir turun süresi yaklaşık 7–9 sn. Bir turda birden çok
  seri model çağrısı olur (niyet sınıflandırma, triage kararı, investigation adımları, anlatım),
  her tool çağrısı yeni bir MCP oturumu açar ve checklist adımları seri çalışır.
- **Neden kabul edilebilir?** Doğruluk ve denetlenebilirlik hıza tercih edildi; bu bir demo. Sonuç
  ekranında SSE akışı bekleme hissini azaltmaya çalışır (ama yukarıdaki A2'ye bak).
- **Production'da?** Bağımsız tool çağrılarını paralelleştirmek, MCP oturumlarını yeniden kullanmak,
  küçük/hızlı modeller ve prompt önbellekleme, gerçek token streaming, bir turdaki model çağrılarını
  birleştirmek (zaten `triage()` üç kararı bir çağrıda alıyor), bir gecikme bütçesi ve izleme (Langfuse).

### C3. Scripted eval modelin kalitesini ölçmez
- **Ne?** Varsayılan eval modunda cevaplar önceden yazılı (`05-test-ve-degerlendirme.md`).
- **Neden kabul edilebilir?** CI'da ucuz, tekrarlanabilir bir regresyon ağı sağlar.
- **Production'da?** Canlı modelle düzenli koşan bir eval kümesi (örn. gecelik), gerçek konuşma
  örnekleriyle büyüyen bir veri seti, insan gözden geçirmesi ve model sürümü karşılaştırması.

---

## Bu listeden ne öğrenmeli?

1. Bir sadeleştirme **söylendiği** sürece sorun değildir; sessiz olduğunda borca dönüşür. Bu yüzden
   projede `Demo simplification:` etiketi var (`docs/STYLE.md`).
2. "Config'te yazıyor" ile "kod uyguluyor" aynı şey değildir. Bir yaml anahtarı eklemeden önce onu
   kimin okuduğunu bul.
3. Bir güvenlik özelliği **kapsamı** kadar güvenlidir (PII maskeleme İngiltere formatlarıyla sınırlı).

Bu maddelerden birini seçip kapatmak, 06-alistirmalar'ın en değerli PR'ları olur.
