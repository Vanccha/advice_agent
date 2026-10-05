# Adding a second tenant

1. Copy this directory to `config/tenants/<tenant>/`.
2. Edit `tenant.yaml`: display name, customer identifier pattern, department codes,
   adapter URLs (one per company system), persona.
3. Edit `policy.yaml`: which actions the assistant may perform for *this* customer,
   monetary caps, and which department each refused action escalates to.
4. Edit `routing.yaml`: recommendation weights and issue→department routing.
5. Only if the new company's systems differ in shape, add adapters under
   `integrations/mcp_<system>/`. The assistant core is untouched.
6. Run with `TENANT=<tenant>`.

No assistant source file may contain the tenant's name.
