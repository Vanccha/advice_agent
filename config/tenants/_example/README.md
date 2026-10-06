# İkinci kiracı — kanıt, şablon değil

Bu klasör boş bir şablon değil: **çalışan ve `nethiz/`'den kasten farklı** ikinci bir
müşteri. "Yeni bir müşteriye geçmek kod değişikliği gerektirmez" iddiasını okumak yerine
çalıştırabilmen için var.

## Neyi farklı yapıyor

| | `nethiz` | `_example` |
|---|---|---|
| Görünen ad | NetHız Telekom | Örnek Fiber A.Ş. |
| Abone numarası formatı | `^NH-\d{6}$` | `^OR-[0-9]{7}$` |
| Faturalama biriminin adı | Faturalama | Gelir Yönetimi |
| Devretme eşiği | 0.6 | **0.8** (daha çabuk insana devreder) |
| Danışma soru sınırı | 5 | **3** |
| Kesinti telafisi | ≤50 TL, onayla **yapabilir** | **yapamaz** → Gelir Yönetimi |
| Kurulum randevusu değiştirme | **yapamaz** → Saha ekibi | **onayla yapabilir** |
| Öneri ağırlığı | hız 0.35 / bütçe 0.25 | hız 0.20 / **bütçe 0.45** |
| İzleme ve kanal adaptörleri | zorunlu | **isteğe bağlı** (bu müşteri tedarikçiye açmıyor) |

## Kanıtı kendin koştur

```bash
# Aynı imaj, aynı kod, yalnızca TENANT farklı:
docker compose run --rm -e TENANT=_example test-runner \
  env PYTHONPATH=/workspace/assistant python -c "
from fastapi.testclient import TestClient
from api.main import app
with TestClient(app) as c:
    print(c.post('/api/login', json={'customer_no': 'NH-100001'}).json())   # INVALID_FORMAT
    print(c.get('/login').text.count('Abone numarası'))                      # kiracının etiketi
"
```
Beklenen: `nethiz`'de geçerli olan `NH-100001` burada **format hatası** alır, giriş ekranı
"Abone numarası" der, yetki motoru telafiyi reddedip randevu değişikliğine izin verir.

## Dürüst sınır

Yetki, eşikler, persona, departman adları, öneri ağırlıkları, hangi adaptörün zorunlu
olduğu — hepsi konfigürasyon. Ancak **sorun tipi ve departman kodları** (`IssueType`,
`Department`) asistanın kodunda enum olarak tanımlı. Yani:

- Başka bir **internet sağlayıcısına** geçmek: yalnızca bu klasör (ve sistemleri farklıysa
  yeni adaptörler).
- Başka bir **sektöre** (enerji, sigorta, banka) geçmek: bu klasöre ek olarak o alanın
  sorun tipi/departman sözlüğünün koda eklenmesi gerekir — adaptörler ve yetki motoru
  olduğu gibi kalır.

## Yeni bir kiracı eklemek

1. Bu klasörü `config/tenants/<müşteri>/` olarak kopyala.
2. `tenant.yaml`: görünen ad, abone numarası formatı, departman kodları ve Türkçe adları,
   adaptör adresleri, persona.
3. `policy.yaml`: bu müşteri için asistanın **ne yapabileceği**, parasal üst sınırlar,
   reddedilen her aksiyonun hangi birime gideceği.
4. `routing.yaml`: öneri ağırlıkları ve sorun tipi → departman yönlendirmesi.
5. Sistemleri farklıysa `integrations/mcp_<sistem>/` altına yeni adaptör ekle.
6. `TENANT=<müşteri>` ile çalıştır.

Asistan kaynak dosyalarının hiçbirinde kiracının adı geçmez; bunu
`tests/architecture/test_boundaries.py` kontrol eder.
