# Tags

Tags are free-form labels attached to test cases. They are global (not scoped to a project) and stored in a dedicated `tags` table with a many-to-many relationship via `test_case_tags`.

## API surface

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/tags` | List all tags (optional `?q=` prefix search, `?limit=`) |
| `POST /api/v1/tags` | Create a tag (idempotent — returns existing on duplicate name) |
| `GET /api/v1/projects/{id}/test-cases?tag_ids=1&tag_ids=2` | Filter test cases by tags (OR semantics) |

## Behavior

- Tag names are normalized to lowercase and trimmed on write.
- `POST /tags` returns `201` for new tags, `200` for existing ones.
- `GET /tags?q=foo` performs case-insensitive prefix search (`ILIKE 'foo%'`).
- The `tag_ids` filter on test cases uses OR semantics: a test case appears if it has **any** of the specified tags.

## Constraints

- Tags are global — no project scoping (logged as potential tech debt).
- No tag rename, delete, or merge endpoints yet.
- No tag colors or grouping.

## Auth

- `GET /tags` — any authenticated user (read_only and above).
- `POST /tags` — tester role and above.
