# Second tenant — proof, not a template

This folder is not an empty template: it is a **working second customer, deliberately
different from `netswift/`**. It exists so that you can run the claim "moving to a new
customer needs no code change" instead of just reading it.

## What it does differently

| | `netswift` | `_example` |
|---|---|---|
| Display name | NetSwift Telecom | Example Fibre Ltd |
| Account number format | `^NS-\d{6}$` | `^OR-[0-9]{7}$` |
| Name of the billing unit | Billing | Revenue Management |
| Handover threshold | 0.6 | **0.8** (hands over to a human sooner) |
| Advisory question limit | 5 | **3** |
| Outage compensation | up to £5, **can** apply with consent | **cannot** → Revenue Management |
| Rescheduling installations | **cannot** → field team | **can** with consent |
| Recommendation weights | speed 0.35 / budget 0.25 | speed 0.20 / **budget 0.45** |
| Monitoring and channel adapters | required | **optional** (this customer does not expose them to suppliers) |

## Run the proof yourself

```bash
# Same image, same code, only TENANT differs:
docker compose run --rm -e TENANT=_example test-runner \
  env PYTHONPATH=/workspace/assistant python -c "
from fastapi.testclient import TestClient
from api.main import app
with TestClient(app) as c:
    print(c.post('/api/login', json={'customer_no': 'NS-100001'}).json())   # INVALID_FORMAT
    print(c.get('/login').text.count('Account number'))                      # the tenant's label
"
```
Expected: `NS-100001`, valid for `netswift`, gets a **format error** here, the sign-in page
says "Account number", and the authority engine denies compensation but allows rescheduling.

## The honest limit

Authority, thresholds, persona, department names, recommendation weights, which adapters are
required — all of it is configuration. However, the **issue types and department codes**
(`IssueType`, `Department`) are defined as enums in the assistant's code. So:

- Moving to another **broadband provider**: only this folder (plus new adapters if their
  systems differ).
- Moving to another **sector** (energy, insurance, banking): on top of this folder, that
  domain's issue type/department vocabulary has to be added to the code — the adapters and
  the authority engine stay as they are.

## Adding a new tenant

1. Copy this folder to `config/tenants/<customer>/`.
2. `tenant.yaml`: display name, account number format, department codes and their display
   names, adapter URLs, persona.
3. `policy.yaml`: **what the assistant may do** for this customer, monetary caps, and which
   unit each denied action goes to.
4. `routing.yaml`: recommendation weights and issue type → department routing.
5. If their systems differ, add new adapters under `integrations/mcp_<system>/`.
6. Run with `TENANT=<customer>`.

The tenant's name appears in none of the assistant's source files;
`tests/architecture/test_boundaries.py` checks this.
