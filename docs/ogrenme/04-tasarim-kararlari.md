# 04. Tasarım Kararları

Her kayıt ADR (Architecture Decision Record, mimari karar kaydı) tarzında kısa: **Bağlam** (sorun
neydi?), **Karar**, **Sonuçlar** (artı ve eksiler), **Alternatifler** (neden seçilmedi?).

Neden ADR? Kod *ne yaptığını* anlatır, ama *neden böyle yaptığını ve neleri reddettiğini* anlatmaz.
Altı ay sonra "bunu neden böyle yazmışız?" diye sorduğunda cevabı burada ararsın.

Önemli not: Burada "alternatifler" bölümlerinde yazanların bir kısmı projenin kendi
belgelerinden, bir kısmı bu rehberin genel mühendislik değerlendirmesidir. Hangisi hangisi
olduğunu metinde belirttik.

İçindekiler: ADR-1 Stack · ADR-2 Asistan şirket DB'sine yazmaz · ADR-3 Deterministik öneri ·
ADR-4 Hibrit teşhis · ADR-5 Policy engine · ADR-6 Tur başına gateway · ADR-7 Audit serileştirme ·
ADR-8 MCP · ADR-9 Kimlik yalnızca cookie'den · ADR-10 Bağımlılık sabitleme

---

## ADR-1: Python + FastAPI + PostgreSQL

**Bağlam.** Proje hem şirket servislerini hem asistanı hem entegrasyonları içeriyor. Çok dilli bir
yığın, tek bir ekip için bakım yükünü artırırdı. İlk plan .NET + MS SQL Server idi.

**Karar.** Her yerde Python 3.12 + FastAPI + PostgreSQL 16 (`CLAUDE.md`, "Stack decisions").

**Sonuçlar.**
- Artı: Tek dil, tek test aracı (pytest), tek ORM (SQLAlchemy). LLM/MCP SDK'ları Python'da
  birinci sınıf destekli.
- Artı: UI'lar sunucu taraflı Jinja + vanilla JS; Node derleme adımı yok.
- Eksi: Testler container'da çalıştığı için host'ta hızlı geri bildirim biraz yavaş.

**Alternatifler.** .NET + SQL Server **reddedildi**: proje sahibinin makinesi arm64 ve SQL Server
imajı yalnızca amd64; emülasyon yavaş ve kararsız (`CLAUDE.md` bu gerekçeyi yazıyor).

---

## ADR-2: Asistan şirket veritabanına asla yazmaz

**Bağlam.** Asistan dışarıdan gelen bir misafir. Bir model hatası, prompt injection veya basit bir
bug şirketin üretim verisini bozabilir. "Dikkatli olacağız" bir güvence değildir.

**Karar.** İki ayrı yol:
- **Okuma:** `integrations/` katmanı `readonly_diag` rolü ile bağlanır; bu rol yalnızca
  `diag.*` view'larında `SELECT` yetkisine sahiptir, `core.*` tamamen kapalıdır
  (`company/core-api/app/bootstrap.py::_grant_statements`).
- **Yazma:** Yalnızca şirketin **kendi REST API'si** üzerinden, kapsamlı (scoped) API key ile
  (`integrations/mcp_core/app/tools_write.py`). Şirket kendi iş kurallarını uygular.

**Sonuçlar.**
- Artı: Yazma engeli **veritabanında** zorlanır (`tests/integration/test_readonly_role.py`); kod
  hatalı olsa bile yazılamaz. Şirketin iş kuralları (doğrulama, olaylar) atlanamaz.
- Artı: `diag.*` view'ları bir sözleşmedir; şirket tablolarını yeniden düzenleyebilir.
- Eksi: Her yeni "okunacak şey" için bir view ve bir tool yazmak gerekir. Bu kasıtlı bir sürtünme.

**Alternatifler.** (Bu rehberin değerlendirmesi.) Asistana doğrudan bir DB kullanıcısı vermek daha
az kod demek ama "veriyi kim, hangi kuralla değiştirdi?" sorusunu ve şirketin güvenlik
modelini bozar.

---

## ADR-3: Öneriler deterministiktir, LLM sadece anlatır

**Bağlam.** "Hangi paketi önermeliyiz?" para ve güven meselesi. Aynı müşteri iki kez sorduğunda
farklı cevap almamalı; neden o paketin önerildiği açıklanabilmeli; ve model katalogda olmayan bir
paket veya yanlış fiyat uydurmamalı.

**Karar.** Puanlama `assistant/recommendation/engine.py::recommend_packages` içinde saf bir
fonksiyondur (ağırlıklar ve gerekçe şablonları `routing.yaml`'dan). LLM yalnızca soru sorar ve
motorun ürettiği `PackageOffer` listesini anlatır; anlatım `validate_model_reply`
(`assistant/modes/advisory/narration.py:79`) ile motor çıktısına karşı doğrulanır, uymazsa sabit
şablona düşülür.

**Sonuçlar.**
- Artı: Test edilebilir (`assistant/recommendation/tests/`, `evals/advisory_suite.py` aynı girdinin
  aynı çıktıyı verdiğini ölçer), açıklanabilir, tenant'a göre ayarlanabilir.
- Artı: Model yanlış yapsa bile yanlış paket müşteriye ulaşmaz.
- Eksi: Serbest metinden müşteri profilini çıkarmak (kaç kişi, bütçe) hâlâ bir ayrıştırma sorunu:
  model + regex yedeği + makullük kontrolü gerekir (`modes/advisory/parsing.py`, `extraction.py`).

**Alternatifler.** "Katalogu LLM'e ver, o seçsin": daha az kod ama tekrarlanabilirlik, denetlenebilirlik
ve uydurma riski yüzünden reddedildi (`modes/advisory/__init__.py` ve `docs/contracts.md` §4.3-4.4 bu
ilkeyi yazıyor).

---

## ADR-4: Hibrit teşhis: önce checklist, sonra salt-okunur LLM tool döngüsü

**Bağlam.** Müşterilerin şikâyetlerinin bir kısmı bilinen arıza kalıplarıdır (takılı provisioning,
çift çekim...) ve ucuz, açıklanabilir kurallarla tanınır. Geri kalanı ("akşamları yavaş", "modemin
ışığı turuncu") önceden kalıba sığmaz. İlk sürümde checklist bir şey bulamayınca "belirgin bir
sorun göremiyorum" diyen ölü bir uç vardı; bu müşteri için kötü bir deneyimdi.

**Karar.** İki aşama:
1. `modes/diagnostic/checklist.py`: yedi sabit adım, deterministik, ilk eşleşmede durur.
2. Hiçbir şey bulunmazsa `modes/investigation/`: sınırlı bir LLM tool döngüsü. Tool'lar
   `routing.yaml: investigation_tools` allowlist'i ile **yalnızca okuma** tool'larıyla sınırlı,
   `customer_no` sunucuda zorlanır, adım ve soru sayısı sınırlı, bitiş şeması `submit_finding`.
   Sonuç aksiyon çalıştırmaz; yalnızca cevabı veya ticket'ı şekillendirir.

**Sonuçlar.**
- Artı: Bilinen durumlar hızlı, ucuz ve tekrarlanabilir. Bilinmeyen durumlar ölü uca düşmez.
- Artı: LLM'in zarar verme alanı yapısal olarak yok: yazan bir tool listede olmadığı için çağrılamaz.
- Eksi: Daha çok hareketli parça; investigation gecikmeyi artırır (her adım bir model çağrısı).
- Eksi: Açık bir investigation sırasında müşteri konu değiştirirse eski checklist sonucu yeniden
  kullanılır (bkz. `07-bilinen-sinirlar.md`).

**Alternatifler.** (a) Sadece kurallar: bilinmeyen durum için çözümsüz. (b) Sadece LLM ajanı:
bilinen kalıpları da pahalı ve tekrarlanamaz şekilde çözerdi; yetki sınırı da daha zor
kanıtlanırdı.

---

## ADR-5: Model yerine policy engine yetkiye karar verir

**Bağlam.** LLM'e "sadece izin verilen şeyleri yap" demek bir **istek**, güvence değildir. Prompt
injection veya basit bir halüsinasyon, "iade yap" aksiyonunu tetikleyebilir.

**Karar.** Her yazma aksiyonu `ActionExecutor.execute` (`assistant/policy/executor.py:60`)
üzerinden geçer; o da `PolicyEngine.check` ile `policy.yaml`'a sorar. Bilinmeyen aksiyon reddedilir
(default-deny). Koşullar (`job.attempt_count < 3`), hız sınırları, tutar tavanları
(`max_amount_gbp`) ve onay zorunluluğu (`requires_confirmation`) yaml'dadır. Reddedilen aksiyon
sessizce kaybolmaz; ticket'a dönüşür.

**Sonuçlar.**
- Artı: Yetki **veri** olarak denetlenebilir ve müşteri başına değişir (`_example` tenant'ında
  tazminat yok, randevu erteleme var).
- Artı: Test edilebilir saf fonksiyon (`assistant/policy/tests/test_engine.py`).
- Eksi: `build_policy_context` (`modes/action/known_causes.py:60`) bağlamı kodla kuruyor; yaml'daki
  bir koşul için bağlamda alan yoksa koşul "sağlanmadı" olur (fail-safe) ama bu, yeni koşul
  eklerken kolayca şaşırtır. (Alıştırma 1 tam bunu işliyor.)

**Alternatifler.** Yetkiyi prompt'a yazmak (reddedildi: zorlanamaz); aksiyonların içine `if`
kontrolleri dağıtmak (reddedildi: unutulur, denetlenemez, müşteri başına değişemez).

---

## ADR-6: Tool gateway'i tur başına kapsamlanır

**Bağlam.** Başlangıçta tek bir paylaşılan `ToolGateway` vardı. Bütçe sayacı ve audit'e yazılan
`conversation_id` o paylaşılan nesnede duruyordu. Eşzamanlı iki konuşma birbirinin tool hakkını
yiyebilir, tool çağrılarının audit kayıtları yanlış konuşmaya düşebilirdi (git geçmişinde commit
`6e9f2c9`: "Scope tool gateway per turn, serialise audit appends...").

**Karar.** Başlangıçtaki gateway yalnızca **kök**: adapter ayarları ve keşfedilen katalog.
Orchestrator her tur için `gateway.for_turn(conversation_id)` ile ucuz bir görünüm üretir
(`assistant/modes/orchestrator.py::_scope`, satır 190); kendi `conversation_id`'si ve bütçesi vardır,
katalog paylaşılır (`docs/contracts.md` §4.3 bunu da yazar).

**Sonuçlar.**
- Artı: Konuşmalar birbirinin bütçesini/audit bağlamını bozamaz; testi `assistant/modes/tests/test_turn_scoping.py`.
- Artı: Katalog bir kez keşfedilir.
- Eksi: "Hangi nesne paylaşılıyor, hangisi tura özel?" sorusu zihinsel yük getirir; dosya başlıkları
  bunu açıklamak için yazıldı.

**Alternatifler.** Her turda tüm gateway'i sıfırdan kurmak (katalog keşfi tekrarlanır, yavaş) veya
paylaşılan nesneyi kilitle korumak (ölçeklenmez, karmaşık).

---

## ADR-7: Audit append'leri serileştirilir

**Bağlam.** Audit zinciri: her satır önceki satırın hash'ine bağlı ve `seq` global. İki yazıcı aynı
anda "son satır" bilgisini okursa aynı öncülden iki satır doğar; zincir çatallanır ve
`verify_chain` yalan söyler.

**Karar.** Postgres'te `pg_advisory_xact_lock` ile ("öncülü oku -> hash hesapla -> ekle -> commit")
dizisi tek anahtar üzerinde serileştirilir (`assistant/audit/log.py::_serialised_session`). Postgres
dışında (birim testleri) süreç içi `threading.Lock` kullanılır. `seq` için DB seviyesinde benzersiz
kısıt yok; benzersizlik serileştirilmiş append'ten gelir (`docs/contracts.md` §1.5, mevcut veritabanları
için migration hikâyesi olmadığı için).

**Sonuçlar.**
- Artı: Çatallanma imkânsız; testi `assistant/audit/tests/test_audit_concurrency.py`.
- Eksi: Tüm audit yazımları global tek bir kilitten geçer. Düşük/orta hacimde sorun yok; yüksek
  hacimde bir darboğaz olur (konuşma başına anahtar veya zincir yerine Merkle ağacı gibi
  alternatifler düşünülebilir, bu rehberin görüşü).

**Alternatifler.** Optimistic retry (hash çakışınca yeniden dene): daha karmaşık ve çatallanma
penceresi bırakır. `SERIALIZABLE` izolasyon: tüm transaction'a yük bindirir.

---

## ADR-8: İki dünya arasında MCP

**Bağlam.** Asistanın şirket sistemlerine erişimi için bir sözleşme gerekiyor: her şirketin
sistemi farklı, ama asistan koduna dokunmadan müşteri değiştirebilmek istiyoruz.

**Karar.** Her şirket sistemi için bir MCP sunucusu (`integrations/mcp_*`). Asistan MCP istemcisi
olarak tool'ları **keşfeder** (`list_tools`) ve çağırır (`assistant/mcp_gateway/gateway.py`);
adresler tenant config'inden gelir.

**Sonuçlar.**
- Artı: Tool'lar kendi şemalarıyla keşfedilir; investigation allowlist'i ile kesişim hesaplanabilir.
  Yeni bir sistem = yeni bir MCP sunucusu; asistan değişmez.
- Artı: Process sınırı doğal bir güvenlik sınırı: adapter'lar ayrı container, ayrı kimlik bilgileri.
- Eksi: Her çağrı ağ geçişi ve (bu projede) yeni bir MCP oturumu açar (`gateway.py::call`,
  `async with streamable_http_client(...)`). Bu, gecikmeye katkı yapar (bkz. `07-bilinen-sinirlar.md`).

**Alternatifler.** (Bu rehberin değerlendirmesi.) Doğrudan REST çağrıları yazmak: daha az hareketli
parça ama tool keşfi, standart şema ve LLM entegrasyonu elle yapılırdı.

---

## ADR-9: Müşteri kimliği yalnızca cookie'den gelir

**Bağlam.** `POST /api/chat` gövdesinde bir `customer_no` alanı vardı. Bu alan istemci tarafından
geldiği için herkes başkası adına konuşabilir ve başkasının teşhis sonuçlarını okuyabilirdi
(commit `df0e17a`: "Harden assistant API: cookie-only identity, ownership checks, signed alert
webhook, lifespan").

**Karar.** Kimlik **yalnızca** `/api/login`'in sunucu tarafından ayarladığı `httponly`,
`samesite=lax` cookie'den okunur (`assistant/api/security.py::session_customer`). Gövdedeki
`customer_no` alanı geriye uyumluluk için kabul edilir ama yok sayılır
(`assistant/api/routes/chat.py::ChatRequest`). Her konuşma bir müşteriye bağlanır ve sahiplik
orchestrator çalışmadan önce kontrol edilir (403); onay kartlarında yabancıya 404 verilir ki id'ler
yoklanamasın.

**Sonuçlar.**
- Artı: "Kimsin?" sorusu istemcinin iddiasına değil sunucunun verdiği çereze dayanır.
- Eksi (bilinçli demo sadeleştirmesi): giriş formunda parola yok; müşteri numarasını bilen biri o
  müşteri olarak girebilir (`07-bilinen-sinirlar.md`). Cookie `secure` değil ve imzasız. Production'da
  OIDC/SSO ve imzalı, süresi dolan oturum gerekir.

**Alternatifler.** Gövdeden kimlik almak (reddedildi: güvensiz). JWT/imzalı oturum: production'da
doğru yön, demoyu gereksiz karmaşıklaştırırdı.

---

## ADR-10: Bağımlılıkları sabitle (FastAPI yield-scope olayı)

> Durum notu: Bu ADR bir **ders** kaydıdır. Bu commit'te `get_db` hâlâ düz `Depends(get_db)` ile
> kullanılıyor ve `requirements.txt` dosyalarında alt sınırlı sürümler var (`fastapi>=0.115`).
> Yani karar henüz kodda uygulanmış değil; alıştırma olarak size bırakıldı.

**Bağlam.** `company/core-api/app/db.py::get_db`, `yield` ile bir oturum verir; `yield`'den sonra
`session.commit()` çağırır. Sürüm notlarına göre yeni FastAPI sürümlerinde (0.118 ve sonrası)
yield sonrası kod, cevap istemciye gönderildikten **sonra** çalışır. Sonuç: endpoint `201` döner,
istemci hemen ardından okur, commit henüz yok. Yani "yazdım ama okuyunca yok" tipi, zamanlamaya
bağlı hatalar, ve commit sonradan patlarsa istemci başarılı sanıp gider. Kod aynı, sadece
`pip install` ile gelen **sürüm** değişti ve `>=` alt sınırı bunu sessizce içeri aldı.

**Karar (önerilen).**
1. Kritik yan etkiyi (commit) cevaptan önce garanti et: yeni sürümlerde
   `Depends(get_db, scope="function")` (0.121+), ya da commit'i endpoint içinde açıkça yap.
2. Üretimde çalışan sürümleri **sabitle** (`fastapi==X.Y.Z`, bir kilit dosyası ile) ve yükseltmeyi
   test paketiyle birlikte bilinçli bir PR olarak yap.

**Sonuçlar.**
- Artı: Davranış tekrarlanabilir; "dün çalışıyordu" olayları azalır.
- Eksi: Güvenlik yamalarını elle takip etmek gerekir (örn. otomatik bir bağımlılık botu ile).

**Alternatifler.** `>=` ile yaşamak: yeni özellikler bedava gelir ama bu olaydaki gibi davranış
değişiklikleri de gelir. Tüm yükseltmeleri dondurmak: güvenlik açıkları birikir.

> Not: Bu ADR'deki sürüm numaraları FastAPI'nin sürüm notlarından hatırlatmadır; kendi
> ortamında `pip show fastapi` ile sürümü ve ilgili sürüm notlarını doğrula.
