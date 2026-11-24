# NetSwift + AI Destek Asistanı: Öğrenme Rehberi

Bu rehber, projeyi ilk kez açan bir junior geliştirici için yazıldı. Amaç, "kod çok büyük, nereden
başlayacağım?" hissini ortadan kaldırmak ve her modülün **neden** öyle yazıldığını anlatmak.

> Dil kuralı: Kodun kendisi, kod içi yorumlar, commit mesajları ve ürün metinleri **İngilizce**
> (sektör standardı). Bu klasördeki rehber ise bilerek **Türkçe**. Teknik terimleri (endpoint,
> commit, webhook, tool calling, state machine…) İngilizce bırakıyoruz, çünkü iş hayatında böyle
> kullanılıyorlar; her birini ilk geçtiği yerde bir kez açıklıyoruz.

## 1. Proje nedir?

Hayali bir İngiliz fiber internet sağlayıcısı var: **NetSwift Telecom**. Şirketin kendi
veritabanları, REST API'leri, ticket sistemi ve izleme (monitoring) altyapısı var. Bir şirket
olarak bu sistemleri yıllardır çalıştırıyor ve **yapay zekâdan haberi yok**.

Biz de bu şirkete **dışarıdan** bir ürün "monte ediyoruz": müşterilerle sohbet eden, paket öneren,
arıza teşhisi yapan ve gerekirse ticket açan bir AI destek asistanı. Gerçek hayatta bir kurumsal
müşteriye böyle bir ürün satıldığında olan şeyin provasını yapıyoruz.

Bu yüzden repoda birbirine **asla karışmaması gereken iki dünya** var:

| | `company/` (NetSwift) | `assistant/` (ürün) |
|---|---|---|
| Rolü | Müşteri şirketin kendi sistemi | Dışarıdan gelen misafir sistem |
| Asistanı bilir mi? | Hayır | NetSwift'in iç yapısını bilmez |
| Veriye erişim | Veritabanının sahibi, serbestçe yazar | Şirket veritabanına **asla yazmaz** |

İkisinin arasındaki köprü `integrations/` klasörü (her şirket sistemi için bir MCP sunucusu) ve
müşteriye özel ayarlar `config/tenants/` altında.

## 2. Neden öğretici?

Bu proje "AI ile sohbet botu yaptık" demosu değil. Aşağıdaki konuların hepsini **tek bir çalışan
örnekte, birbirine bağlı** olarak görebilirsin:

- Modelin yetkisini model karar vermez, **policy engine** karar verir (default-deny).
- Öneriler LLM'den değil **deterministik** bir fonksiyondan çıkar; LLM sadece anlatır.
- Teşhis iki aşamalı: önce kural tabanlı checklist, sonra sınırlı ve salt-okunur bir LLM tool loop.
- Kişisel veri (PII) modele, trace'e ve ticket'a gitmeden önce maskelenir.
- Audit log hash zinciriyle bağlı ve veritabanı trigger'ı ile değiştirilemez.
- "Sınır testleri" ile mimari kurallar CI'da zorlanır.
- Hata enjeksiyonu (chaos) ve değerlendirme (eval) ile davranış ölçülür.

Her modülün en üstünde bir **öğretim başlığı** (purpose / Where this sits / Key ideas / Read next)
var. Bu rehber o başlıkların üstüne bir "büyük resim" ekler.

## 3. Kimin için, önkoşullar

Hedef kitle: 0–2 yıl deneyimli geliştiriciler. Bilmen gerekenler:

- **Python** temelleri (fonksiyon, sınıf, type hint, `pytest` ile test yazma fikri)
- **HTTP/REST**: endpoint, status code, header, cookie, JSON
- **SQL**: SELECT/INSERT/UPDATE, transaction fikri, view ve index'in ne olduğu
- **Docker** temelleri: image, container, `docker compose up`

Bilmen **gerekmeyenler** (rehber yolda anlatıyor): FastAPI, SQLAlchemy, pydantic, MCP, LLM
tool calling, SSE, HMAC. Bunları `03-kavramlar-sozlugu.md` içinde bulabilirsin.

## 4. Kurulum

### 4.1 Gerekenler

- **Docker Desktop** (Windows'ta WSL2 backend ile). Başka bir şey gerekmiyor: testler ve
  servisler container içinde çalışıyor, host'taki Python sürümü önemli değil.
- Bir **OpenAI API anahtarı** (sadece canlı sohbeti denemek için; testler ve eval anahtar
  istemez).

### 4.2 OpenAI anahtarı ve `.env`

Repo kökünde `.env.example` var. Onu `.env` olarak kopyala ve `OPENAI_API_KEY` satırına kendi
anahtarını yaz:

```powershell
# PowerShell
Copy-Item .env.example .env
```

```text
OPENAI_API_KEY=sk-...senin-anahtarin...
```

Varsayılanlar: `LLM_PROVIDER=openai_agents`, `LLM_MODEL=gpt-4.1-mini`. `.env` dosyasını **asla
commit'leme** (içinde anahtarın var).

### 4.3 `make` yoksa (Windows)

Makefile sadece kısayol. Windows'ta `make` genelde kurulu olmuyor; ya WSL / Git Bash içinden `make`
kullanırsın ya da aşağıdaki `docker compose` komutlarını doğrudan çalıştırırsın. Hepsi `Makefile`
ile birebir aynı işi yapar (`Makefile` içindeki hedeflerle karşılaştırılarak yazıldı).

| `make` hedefi | Doğrudan komut | Not |
|---|---|---|
| `make env` | `Copy-Item .env.example .env` | `.env` yoksa oluşturur |
| `make up` | `docker compose up -d --build` | Makefile ayrıca sağlık kontrolü bekler (`wait`); elle bakmak için `curl http://localhost:8080/health` |
| `make smoke` | `bash scripts/smoke.sh` | Git Bash/WSL gerekir; içinde `curl` ve `python3` kullanıyor. Alternatif: tarayıcıda `http://localhost:8001/docs` ve `http://localhost:8080` aç |
| `make test` | `docker compose run --rm test-runner bash scripts/run_tests.sh` | Tüm suite'ler, her biri ayrı süreçte |
| `make test-unit` | `docker compose run --rm test-runner bash scripts/run_tests.sh -m "not integration"` | Stack gerekmez |
| `make test-arch` | `docker compose run --rm test-runner python -m pytest tests/architecture` | Sadece mimari sınır testleri |
| `make test-integration` | `docker compose run --rm test-runner python -m pytest tests/integration -m integration` | Önce `up` gerekir |
| `make chaos SCENARIO=stuck_provisioning` | `docker compose run --rm chaos python -m chaos.cli stuck_provisioning` | Senaryo adını değiştir |
| `make chaos-status` | `docker compose run --rm chaos python -m chaos.cli status` | Aktif senaryolar |
| `make chaos-reset` | `docker compose run --rm chaos python -m chaos.cli reset` | Her şeyi geri alır |
| `make eval` | `docker compose run --rm test-runner python -m evals.run --report evals/reports` | Stack ayakta olmalı |
| `make logs SERVICE=assistant` | `docker compose logs -f --tail=100 assistant` | |
| `make down` | `docker compose down` | Veriyi korur (`-v` eklersen volume'lar da silinir) |
| `make open` | (URL'leri aşağıdan al) | |

Mimari testleri (`tests/architecture`) sadece AST (kod ağacı) tarayıp metin aradığı için Docker
olmadan da çalışır: `pip install pytest` sonra `python -m pytest tests/architecture`. Repo kökündeki
`pyproject.toml` içinde `--timeout` seçeneği tanımlı; `pytest-timeout` kurulu değilse
`-o addopts=""` ekle.

### 4.4 İlk çalıştırma

```powershell
docker compose up -d --build
# 1-2 dakika sonra:
curl http://localhost:8001/health
curl http://localhost:8080/health
```

| Arayüz | Adres |
|---|---|
| Müşteri sohbet widget'ı | http://localhost:8080 |
| Departman ticket paneli | http://localhost:8003/agent |
| Departman kanalları (Teams/Slack yerine) | http://localhost:8004 |
| Şirket API dokümanı (OpenAPI) | http://localhost:8001/docs |
| Prometheus / Alertmanager | http://localhost:9091 · http://localhost:9093 |

Widget'ta oturum açmak için bir müşteri numarası gerekiyor (`NS-100001` gibi). Giriş ekranında
parola yok; bu bilinçli bir demo sadeleştirmesi (bkz. `07-bilinen-sinirlar.md`).

## 5. Önerilen okuma sırası: 2 haftalık plan

Günde 2–3 saat varsayıyoruz. "Oku" dediğimiz yerde dosyanın en üstündeki öğretim başlığını
(docstring) önce oku, sonra koda geç.

### Hafta 1: Büyük resim ve çekirdek akış

| Gün | Rehber | Kodda bak | Yap |
|---|---|---|---|
| 1 | Bu dosya, `01-mimari-turu.md` | `CLAUDE.md`, `README.md`, `tests/architecture/test_boundaries.py` | Stack'i kaldır, widget'ta bir "merhaba" yaz |
| 2 | `02-bir-mesajin-yolculugu.md` (giriş + A senaryosu) | `assistant/api/routes/chat.py`, `assistant/modes/orchestrator.py`, `assistant/modes/dispatch.py` | Bir konuşma yap, sonra `docker compose logs assistant` bak |
| 3 | `02-...` (B senaryosu) | `assistant/modes/diagnostic/checklist.py`, `assistant/modes/action/known_causes.py` | `chaos stuck_provisioning` çalıştır, widget'ta şikâyet et |
| 4 | `03-kavramlar-sozlugu.md` (A–M) | `assistant/policy/engine.py`, `assistant/policy/executor.py`, `config/tenants/netswift/policy.yaml` | `policy/tests/test_engine.py` testlerini oku |
| 5 | `03-...` (N–Z) | `assistant/audit/log.py`, `assistant/privacy/masking.py` | Widget'taki "What did the assistant do?" panelini incele |

### Hafta 2: Derinlik, test ve pratik

| Gün | Rehber | Kodda bak | Yap |
|---|---|---|---|
| 6 | `04-tasarim-kararlari.md` | `assistant/modes/investigation/`, `assistant/recommendation/engine.py` | Karar kayıtlarından birini kendi cümlelerinle özetle |
| 7 | `05-test-ve-degerlendirme.md` | `scripts/run_tests.sh`, `evals/README.md`, `evals/datasets/scenarios.yaml` | `make test-arch` ve `make eval` çalıştır |
| 8 | `07-bilinen-sinirlar.md` | `assistant/api/routes/chat.py`, `assistant/api/routes/auth.py` | Her sınır için "production'da ne yapardık?" sorusunu yanıtla |
| 9 | `06-alistirmalar.md` (Kolay + Orta) | İlgili alıştırmadaki dosyalar | 2 alıştırma seç ve bitir |
| 10 | `06-alistirmalar.md` (Orta/Zor) | | 1 alıştırmayı PR gibi hazırla; `docs/STYLE.md` ile gözden geçir |

## 6. Rehberdeki dosyalar

| Dosya | İçerik |
|---|---|
| `README.md` | Bu giriş: kurulum, okuma planı |
| `01-mimari-turu.md` | İki dünya, katmanlar, değişmezler (invariants) ve onları zorlayan mekanizmalar, multi-tenancy |
| `02-bir-mesajin-yolculugu.md` | Bir müşteri mesajının uçtan uca izlenmesi (iki senaryo + advisory yolu) |
| `03-kavramlar-sozlugu.md` | Alfabetik terim sözlüğü: tanım, projedeki yeri, neden önemli |
| `04-tasarim-kararlari.md` | ADR tarzı kısa karar kayıtları |
| `05-test-ve-degerlendirme.md` | Test türleri, nasıl çalıştırılır, flaky testler, eval'in anlamı |
| `06-alistirmalar.md` | Kolay/Orta/Zor alıştırmalar |
| `07-bilinen-sinirlar.md` | Demo sadeleştirmeleri ve bilinen sınırlar |
| `../STYLE.md` | (İngilizce) Kod yorum ve isimlendirme standardı |

Proje içindeki asıl "doğruluk kaynakları": `CLAUDE.md` (kurallar), `docs/contracts.md` (şemalar,
endpoint'ler, payload'lar), `evals/README.md` (değerlendirme düzeneği). Bu rehber onların yerini
almaz, onlara giden yolu kolaylaştırır. Rehber ile kod çelişirse **kod haklıdır**; fark ettiğinde
düzeltme PR'ı aç.
