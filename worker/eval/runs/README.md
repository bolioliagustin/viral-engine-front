# Corridas del tier `e2e`

Una línea por corrida. El JSON completo (por clip y agregados) queda al lado;
el `.log` solo se versiona si pesa < 1 MB. Cómo correr y comparar:
[`../README.md`](../README.md) (sección "Tier e2e"). La regla de oro de la
rueda de mejora ([`docs/PLAN_CALIDAD.md`](../../../docs/PLAN_CALIDAD.md) §5):
**no se cambia nada del pipeline de IA sin un run e2e antes y después.**

Convención de nombre: `<fecha>-<PROMPT_VERSION>[-nota].json`.

| Fecha | Archivo | PROMPT_VERSION | Modelos (analysis / copy / judge / classifier) | Commit | Qué se cambió | Resultado (juez avg · ≥7 · verif_failed · densidad fuera · costo · tiempo) |
|---|---|---|---|---|---|---|
