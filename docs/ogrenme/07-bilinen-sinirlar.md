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

### B5. `policy.yaml` limitleri ve koşulları: bir inceleme bulguları ve düzeltmeleri
Bir kod incelemesi, projenin temel vaadini ("model kendi yetkisine karar vermez, policy engine
verir") zayıflatan boşluklar buldu. Hepsi düzeltildi; hangi boşluk neydi, ne yapıldı ve neden:
- **Uydurma policy gerçekleri.** `build_policy_context` `credit.existing_count_30d` değerini sabit `0`,
  `amount_gbp` değerini sabit `5.0` veriyordu: "son 30 günde kredi almamış olmalı" koşulu **her
  zaman** geçiyordu. Şimdi tutar aksiyonun gerçek `params`'ından gelir; kredi sayısı yeni salt-okunur
  `get_credit_history` tool'uyla (`diag.credit_history` view'u) şirketin kayıtlarından okunur
  (yalnızca `apply_outage_credit` için çağrılır). Veri alınamazsa alan bağlama konmaz ve policy
  engine reddeder; `0` varsayılmaz. Not: tutar hâlâ sabit bir demo sadeleştirmesidir
  (`OUTAGE_CREDIT_GBP`), ama policy'nin kontrol ettiği sayı ile çalışan sayı artık aynıdır.
- **Hiç tetiklenmeyen hız sınırları.** `rate_limit` `context["usage"]` okuyordu ama kimse doldurmuyordu.
  Şimdi `ActionExecutor` her kontrolden önce `policy/usage.py` ile asistanın **kendi** `ActionRecord`
  kayıtlarından (`executed=True`) konuşma başına ve müşteri başına UTC günlük sayaçları hesaplar;
  çağıranın verdiği `usage` yok sayılır.
- **Uygulanmayan `max_actions_per_conversation`.** `StateMachine.check_actions_per_conversation`
  yalnızca testlerde çağrılıyordu. Şimdi `ActionExecutor` bir aksiyon izin alıp çalışmadan (ve onay
  istenmeden) önce bunu çağırır; `LimitExceeded` orkestratörde (ve onay akışında) kibar bir devre
  çevrilir. `check_tool_calls` ve `check_questions_advisory` **silindi**: tool bütçesini gateway
  (`ToolBudgetExceeded`), soru sınırını advisory akışı zaten uyguluyordu; aynı kuralı iki yerde
  tutmak ölü kod ve yanlış güven yaratıyordu.
- **`_example` ve `reschedule_installation`.** Policy onayla izin veriyor ama `ACTION_TOOL_MAP`'te
  eşleme yoktu (`UnmappedActionError`); ayrıca partner anahtarı `appointments:write` yetkisine sahip
  olmadığı için mcp-core tool'u `SCOPE_DENIED` döner. Yetki sınırını gevşetmek yerine `_example`
  policy'si dürüst yapıldı (reddet + `FIELD_INSTALL`'a devret). Tekrarını önlemek için bir config
  tutarlılık testi eklendi (`assistant/mcp_gateway/tests/test_config_consistency.py`, Alıştırma 8).
- **Ölü config: `routing.yaml: diagnostic_checklist`.** Doğrulanıyor ama hiç okunmuyordu; sıra kodda
  sabitti. Adımlar birbirine bağlı (örn. kurulum kontrolü yalnızca abonelik o aşamadaysa çalışır), bu
  yüzden sıra config olmayı hak etmiyor: anahtar iki tenant'tan, config modelinden ve contracts'tan
  kaldırıldı, docstring düzeltildi.
- **Ölü ayar: `MAX_TOOL_CALLS_PER_TURN`.** Çalışma zamanında okunmuyordu; bütçe `policy.yaml:
  limits.max_tool_calls_per_turn`'den gelir. Ayar `settings`, `.env.example` ve compose'tan kaldırıldı.
- **Neden böyle oldu?** Bu boşlukları ne testler ne eval'ler yakaladı: policy testleri bağlamı elle
  veriyordu, eval'lerde kredi geçmişi hep boştu. Ders: bir kuralın testi, kuralın **beslendiği
  verinin** gerçekten üretildiğini de kapsamalı (uçtan uca testler: `modes/tests/test_policy_facts.py`,
  `policy/tests/test_usage_and_limits.py`).
- **Kalan sınırlar.** Kredi tutarı hâlâ sabit (demo sadeleştirmesi); müşteri başına günlük sayaç
  yalnızca `asst.conversations` ile müşteriye bağlı konuşmaları sayar (ilk turdaki konuşma, tur
  bitip kaydedilene kadar `params`'taki `customer_no` ile eşleşir). Gerçek koruma yine şirketin
  kendi API'sinde de olmalı (mcp-core idempotency key gönderir, core-api tavan uygular).
- **Production'da?** Her config anahtarı için "okunuyor mu, uygulanıyor mu?" kontrolü, gerçek kullanım
  sayaçları ve config ile kod uyumunu doğrulayan testler (Alıştırma 8).

### B6. Ek küçük gözlemler
- Birçok dosya başlığında bölüm işareti için `§` karakteri bozuk kodlanmış (`§`) görünüyor
  (örn. `assistant/policy/engine.py` başlığındaki "See: docs/contracts.md §4.6"). Bu bir
  karakter kodlama sorunu; içeriği etkilemez ama aramayı zorlaştırır.
- Bir tur için tool bütçesi yalnızca `policy.yaml: limits.max_tool_calls_per_turn`'dir (netswift: 14);
  `FakeGateway`'in kurucu varsayılanı (8) sadece testler içindir, testler `make_gateway` ile 14 verir.

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
