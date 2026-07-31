---
title: Use tsvector for Full-Text Search
impact: MEDIUM
impactDescription: Avoid full scans and add linguistic ranking when the workload needs it
tags: full-text-search, tsvector, gin, search
---

## Use tsvector for Full-Text Search

B-tree indexes do not accelerate leading-wildcard `LIKE`. A `pg_trgm` GIN or GiST index can accelerate wildcard matching, while full-text search is preferable when the workload needs linguistic tokenization, query operators, or ranking.

**Incorrect (unindexed LIKE pattern matching):**

```sql
-- Without pg_trgm, a leading wildcard cannot use a B-tree index
select * from articles where content like '%postgresql%';

-- A matching expression or trigram index is still required
select * from articles where lower(content) like '%postgresql%';
```

**Correct (full-text search with tsvector):**

```sql
-- Add tsvector column and index
alter table articles add column search_vector tsvector
  generated always as (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(content,''))) stored;

create index articles_search_idx on articles using gin (search_vector);

-- Fast full-text search
select * from articles
where search_vector @@ to_tsquery('english', 'postgresql & performance');

-- With ranking
select *, ts_rank(search_vector, query) as rank
from articles, to_tsquery('english', 'postgresql') query
where search_vector @@ query
order by rank desc;
```

Search multiple terms:

```sql
-- AND: both terms required
to_tsquery('postgresql & performance')

-- OR: either term
to_tsquery('postgresql | mysql')

-- Prefix matching
to_tsquery('post:*')
```

Reference: [Full Text Search](https://supabase.com/docs/guides/database/full-text-search)

Alternative: [pg_trgm index support](https://www.postgresql.org/docs/current/pgtrgm.html#PGTRGM-INDEX)
