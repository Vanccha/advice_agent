# Observability — self-hosted Langfuse

> Bu dosya `docker-compose.observability.yml` dosyasının ne yaptığını açıklar. Ana stack
> (`docker-compose.yml`) ile sıkı biçimde ayrılmıştır: `assistant` servisi bu overlay'e
> bağımlı değildir ve overlay çalışmasa bile çalışmaya devam eder.

## 1. Ne çalıştırıyoruz

`docker-compose.observability.yml`, Langfuse'un resmi self-hosting
docker-compose dosyasının (`https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml`,
**image tag `4`**, 2026-10-05 tarihinde okunan sürüm) bileşenlerinin aynısını, bu repodaki
isimlendirme stiline uyarlanmış servis adlarıyla çalıştırır. Hiçbir bileşen uydurulmadı; her
container upstream'in kendi compose dosyasındaki karşılığının birebir image'ını kullanır.

| Servis (bu repoda) | Upstream image | Ne yapar |
|---|---|---|
| `langfuse-db` | `postgres:17` | Langfuse'un **kendi** metadata veritabanı: org/project/user/API key/prompt kayıtları. `company-db` veya `assistant-db` ile **hiçbir** ilişkisi yoktur; ayrı container, ayrı volume (`langfuse-db-data`). |
| `langfuse-clickhouse` | `clickhouse/clickhouse-server:25.12` | Trace/observation/score olaylarının tutulduğu kolon tabanlı depo (yüksek hacimli analitik sorgular için). |
| `langfuse-redis` | `redis:7` | Worker'ın BullMQ kuyruk arka ucu (ingestion işleme sırası). |
| `langfuse-minio` | `cgr.dev/chainguard/minio` | S3 uyumlu nesne depolama: olay payload'ları (`events/`) ve medya (`media/`) buraya yazılır. Upstream bu image'ı sabit bir etiketle pinlemiyor (rolling), biz de aynı şekilde bıraktık. |
| `langfuse-worker` | `docker.langfuse.com/langfuse/langfuse-worker:4` | Arka plan işçisi: ClickHouse'a yazma, batch export, ingestion queue. Kendi başına UI sunmaz. |
| `langfuse-web` | `docker.langfuse.com/langfuse/langfuse:4` | Langfuse UI + genel API (trace'leri gönderdiğimiz uç nokta burasıdır). |

Her container için healthcheck tanımlıdır (`pg_isready`, ClickHouse `/ping`, `mc ready local`,
`redis-cli ping`, web/worker için `/api/public/health` ve `/api/health`), ve `langfuse-worker`
ile `langfuse-web`, ClickHouse + MinIO + Redis + Postgres `service_healthy` olmadan **başlamaz**
(`depends_on: condition: service_healthy`) — böylece `make up-observability` manuel yeniden
deneme gerektirmeden kendiliğinden düzene oturur.

## 2. Çalıştırma

```bash
make up-observability
# == docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

Bu komut hem ana stack'i (company + assistant) hem de Langfuse'u aynı Docker ağında (proje adı
`nethiz`) başlatır; bu yüzden `assistant` container'ı içeriden `http://langfuse-web:3000`
adresine erişebilir.

- **Langfuse UI**: `http://localhost:${LANGFUSE_WEB_PORT_HOST:-3000}` (varsayılan
  `http://localhost:3000`)
- **Giriş bilgileri** (`.env.example` → `LANGFUSE_INIT_USER_EMAIL` /
  `LANGFUSE_INIT_USER_PASSWORD`): `demo@ornek-eposta.test` / `demo_password_123`
  (`LANGFUSE_INIT_*` değişkenleri sayesinde org/project/user/API key ilk açılışta otomatik
  oluşturulur — demo için elle "sign up" yapmaya gerek yoktur).
- Yalnızca üç port host'a açılır (hepsi `.env.example`'daki değişkenlerle): web `3000`
  (`LANGFUSE_WEB_PORT_HOST`), MinIO S3 API `9090` (`LANGFUSE_MINIO_PORT_HOST`), Langfuse'un
  kendi Postgres'i `55434` (`LANGFUSE_DB_PORT_HOST`, doğrudan `psql` ile bakmak isteyenler için;
  `company-db`'nin `55432` ve `assistant-db`'nin `55433` host portlarıyla çakışmaz). ClickHouse
  (`8123`, `9000`), Redis (`6379`) ve MinIO konsolu (`9091`) yalnızca `127.0.0.1`'e bağlıdır;
  bu portlar demo için gerekli değildir, sadece yerel hata ayıklama (debugging) amaçlıdır.
- Durdurmak için (veriyi silmeden): ilgili container'ları `stop` edin; `docker compose down`
  **kullanmayın** — bu repo'da başka servisler paralel çalışıyor olabilir.

## 3. Assistant trace'leri nasıl gönderiyor

`assistant/observability/` (bkz. `docs/contracts.md` §4.9), OpenAI Agents SDK çağrılarını
`openinference-instrumentation-openai-agents` ile otomatik enstrümante eder; bu enstrümantasyon
OpenTelemetry span'leri üretir ve bunlar Langfuse SDK v3'ün OTel köprüsü üzerinden Langfuse'a
akar:

1. `openinference-instrumentation-openai-agents`, her ajan/tool çağrısını bir OTel span'i
   olarak yayınlar.
2. Langfuse Python SDK v3 `get_client()` çağrısı, bu süreçteki global OTel span processor'ını
   Langfuse'un OTLP uç noktasına (`LANGFUSE_BASE_URL`) bağlar.
3. Her sohbet turu `propagate_attributes(user_id=<maskelenmiş müşteri referansı>,
   session_id=<conversation_id>, tags=[tenant, mode, chaos_scenario or "none"],
   metadata={...maskelenmiş...})` bağlamı içinde çalışır, böylece her trace'e doğru
   kullanıcı/oturum/etiket bilgisi iliştirilir.

Gerekli Python paketleri (hepsi **zaten** `assistant/requirements.txt` içinde — doğrulandı,
eklenecek bir şey yok):

```
langfuse>=3.0
openinference-instrumentation-openai-agents>=0.1
opentelemetry-sdk>=1.29
opentelemetry-exporter-otlp-proto-http>=1.29
openai-agents>=0.2
```

İlgili ortam değişkenleri (`.env.example`, `docker-compose.yml` → `assistant` servisi):

| Değişken | Anlamı |
|---|---|
| `LANGFUSE_ENABLED` | `false` ise `init_tracing()` no-op'tur; hiç OTel/Langfuse çağrısı yapılmaz. |
| `LANGFUSE_BASE_URL` | Varsayılan `http://langfuse-web:3000` (container-içi DNS; overlay çalışırken geçerli). |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Projenin API anahtarları. Bu overlay, Langfuse'u **aynı** değerlerle bootstratlar (`LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`), yani assistant hiçbir anahtarı elle kopyalamadan ilk açılıştan itibaren trace gönderebilir. |

## 4. KVKK notu

`assistant/privacy/masking.py`, veriler bir model sağlayıcısına, trace'e veya tickete ulaşmadan
**önce** uygulanır (bkz. CLAUDE.md kural 8). Yani Langfuse'a ulaşan hiçbir alanda ham kimlik
numarası, telefon, kart bilgisi vb. yoktur — yalnızca maskelenmiş veri. Her trace şu etiketlerle
(`tags`) işaretlenir, böylece UI'da filtrelenebilir:

- `tenant` (örn. `nethiz`)
- maskelenmiş müşteri referansı (`user_id` olarak, örn. `NH-1000xx` değil, maskelenmiş biçimi)
- `mode` (router / advisory / diagnostic / action)
- `chaos_scenario` (aktif senaryo adı veya `"none"`)

## 5. Doğrulama

1. `make up-observability` çalıştırın ve 1–3 dakika bekleyin (Postgres/ClickHouse migrasyonları
   ilk açılışta zaman alır).
2. `http://localhost:3000` adresini açın → `LANGFUSE_INIT_USER_EMAIL` /
   `LANGFUSE_INIT_USER_PASSWORD` ile giriş yapın (otomatik oluşturulmuş proje zaten seçili
   gelir).
3. Assistant ile bir sohbet başlatın (`LANGFUSE_ENABLED=true` olmalı) → Langfuse UI'da
   **Traces** sekmesine gidin → `tenant`, maskelenmiş müşteri referansı, `mode` veya
   `chaos_scenario` etiketlerine göre filtreleyin → turun span'lerini (ajan adımları, tool
   çağrıları, maskelenmiş metadata) görün.

## 6. `LANGFUSE_ENABLED=false` veya Langfuse erişilemezken

`init_tracing()` **fail-open**'dır: Langfuse host'una ulaşılamıyorsa (overlay çalışmıyor, ağ
sorunu vb.) bu durum bir kere loglanır ve asla exception fırlatmaz. Assistant normal şekilde
çalışmaya devam eder; trace'ler bu durumda `/app/var/traces/` altında JSONL dosyalarına yazılır
(yerel fallback sink). Yani Langfuse stack'i kapalıyken de demo hiçbir şekilde bozulmaz.
