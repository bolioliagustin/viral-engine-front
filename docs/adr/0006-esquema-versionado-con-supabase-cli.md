# El esquema de Supabase se versiona con Supabase CLI

Estado: aceptado el 2026-09-16, pendiente de implementación.

Las tablas base (`users`, `jobs`, `content_results`, `transactions`) se crearon a mano en el dashboard y nunca se versionaron; solo existen 15 archivos `supabase_migration_*.sql` sueltos en la raíz que se ejecutaron manualmente. Se decide adoptar Supabase CLI: un `supabase db pull` del proyecto actual como migración base y, desde entonces, toda migración se crea y aplica por CLI en `supabase/migrations/`. Los SQL históricos se conservan en `supabase/legacy/` solo como referencia. Se eligió frente a un `pg_dump` manual porque el CLI es lo que permite levantar el proyecto de desarrollo idéntico al de la beta y aplicar cambios de forma repetible.

## Consequences

- Requiere Supabase CLI en la máquina de desarrollo y en cualquier agente que toque el esquema.
- Los cambios de esquema fuera del CLI (dashboard) quedan prohibidos.
