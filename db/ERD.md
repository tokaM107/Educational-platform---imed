# Entity relationship diagram

Generated from the live schema. Keep it in step with `db/schema.sql`.

Legend: `PK` primary key · `FK` foreign key · `UK` unique · **CASCADE** / **SET NULL**
are the `ON DELETE` behaviours.

## The catalog spine

What a conversational catalog search walks. `lectures.course_id` is kept
alongside `module_id` on purpose — enrolment, subscriptions, reports and the
engagement replay all hang off course→lecture and must not depend on a module
that may not exist.

```mermaid
erDiagram
    users    ||--o{ courses  : "doctor_id"
    subjects ||--o{ courses  : "subject_id"
    courses  ||--o{ modules  : "course_id CASCADE"
    modules  ||--o{ lectures : "module_id SET NULL"
    courses  ||--o{ lectures : "course_id (kept)"

    users {
        int id PK
        varchar role "CHECK student|doctor"
        varchar name
        varchar email UK
    }
    subjects {
        int id PK
        varchar name UK
    }
    courses {
        int id PK
        int doctor_id FK
        int subject_id FK "nullable"
        smallint academic_year "CHECK 1-7, nullable"
        varchar title
    }
    modules {
        int id PK
        int course_id FK
        varchar title "UK with course_id"
        smallint position
    }
    lectures {
        int id PK
        int doctor_id FK
        int course_id FK
        int module_id FK "nullable"
        varchar title
        text video_url
    }
```

## Full schema

```mermaid
erDiagram
    users ||--o{ courses : teaches
    users ||--o{ lectures : teaches
    users ||--o{ enrollments : enrols
    users ||--o{ subscriptions : "pays / is paid"
    users ||--o{ video_events : watches
    users ||--o{ question_attempts : answers
    users ||--o{ reports : "is about"
    users ||--o{ report_narratives : "is about"
    users ||--o{ notifications : receives

    subjects ||--o{ courses : classifies
    courses ||--o{ modules : "contains CASCADE"
    courses ||--o{ lectures : contains
    modules ||--o{ lectures : "groups SET NULL"
    courses ||--o{ enrollments : "CASCADE"
    courses ||--o{ reports : "CASCADE"
    courses ||--o{ report_narratives : "CASCADE"

    lectures ||--o{ transcript_chunks : "CASCADE"
    lectures ||--o{ questions : "CASCADE"
    lectures ||--o{ video_events : "CASCADE"
    lectures ||--o{ reports : "CASCADE"

    topics ||--o{ questions : tags
    questions ||--o{ question_attempts : "CASCADE"
    reports ||--o{ notifications : "CASCADE"
```

`query_embeddings` has no foreign key: it is a standalone cache keyed by
`(query_hash, model, dim)`.

## Every foreign key

| Child | Column | Parent | ON DELETE |
| --- | --- | --- | --- |
| courses | doctor_id | users | — |
| courses | subject_id | subjects | — |
| modules | course_id | courses | CASCADE |
| lectures | doctor_id | users | — |
| lectures | course_id | courses | — |
| lectures | module_id | modules | SET NULL |
| enrollments | student_id | users | — |
| enrollments | course_id | courses | CASCADE |
| subscriptions | student_id | users | — |
| subscriptions | doctor_id | users | — |
| transcript_chunks | lecture_id | lectures | CASCADE |
| video_events | student_id | users | — |
| video_events | lecture_id | lectures | CASCADE |
| questions | lecture_id | lectures | CASCADE |
| questions | topic_id | topics | — |
| question_attempts | student_id | users | — |
| question_attempts | question_id | questions | CASCADE |
| reports | student_id | users | — |
| reports | course_id | courses | CASCADE |
| reports | lecture_id | lectures | CASCADE |
| report_narratives | student_id | users | — |
| report_narratives | course_id | courses | CASCADE |
| notifications | user_id | users | — |
| notifications | student_id | users | — |
| notifications | report_id | reports | CASCADE |
