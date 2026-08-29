# تقدير تكلفة الذكاء الاصطناعي الشهرية

> آخر مراجعة للأسعار: **29 أغسطس 2026**. هذا تقدير قابل للتعديل، وليس فاتورة من Google.

## الخلاصة السريعة

الإعداد الحالي يستخدم `gemini-3.1-flash-lite` للمحادثة و`gemini-embedding-2`
للبحث الدلالي. في السيناريو المتوقع أدناه، التكلفة التقريبية هي:

| السيناريو | الأسئلة شهرياً | تكلفة AI بالدولار | بالجنيه تقريباً | لكل طالب نشط | لكل سؤال |
|---|---:|---:|---:|---:|---:|
| تجربة صغيرة: 100 طالب يومياً | 15,000 | $39.82 | 2,003 EGP | $0.398 | $0.00265 |
| استخدام متوسط: 1,000 طالب يومياً | 150,000 | $397.67 | 19,998 EGP | $0.398 | $0.00265 |
| استخدام كبير: 10,000 طالب يومياً | 1,500,000 | $3,976.19 | 199,954 EGP | $0.398 | $0.00265 |

التحويل المستخدم هو **1 USD = 50.288 EGP**، وهو سعر الدولار المنشور بتاريخ
27 أغسطس 2026 من [منصة نافذة، نقلاً عن مصلحة الجمارك المصرية](https://nafeza.gov.eg/en/currencies).
سعر الصرف يتغير، لذلك يجب تحديثه قبل إعداد ميزانية فعلية.

## مصادر الأسعار الرسمية

- [صفحة أسعار Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing)
- [الدليل الرسمي لعدّ الـtokens](https://ai.google.dev/api/tokens)
- [الـbilling الرسمي](https://ai.google.dev/gemini-api/docs/billing)
- [حدود الاستخدام والـquota](https://ai.google.dev/gemini-api/docs/rate-limits)

| الخدمة | Standard input لكل مليون token | Standard output لكل مليون token | Batch | ملاحظات |
|---|---:|---:|---:|---|
| `gemini-3.1-flash-lite` text | $0.25 | $1.50، وتشمل thinking tokens | $0.125 input / $0.75 output | Free tier مذكور كـfree of charge، وحدوده تتغير حسب الـproject/tier |
| `gemini-embedding-2` text | $0.20 | لا يوجد output | $0.10 input | السعر هنا للنص؛ الصورة والصوت والفيديو لهم أسعار مختلفة |
| Context caching لنفس chat model | $0.025 cached input | — | $0.0125 في Batch | التخزين Standard: $1 لكل مليون token لكل ساعة |

صفحة الأسعار الحالية لا تعرض long-context tier مختلف لـ`gemini-3.1-flash-lite`؛
السعر المنشور ثابت. هذا لا يعني أن كل model مستقبلي سيكون كذلك: مثلاً بعض موديلات
Pro لها سعر أعلى بعد حد معين. عند تغيير `CHAT_MODEL` يجب مراجعة الصفحة من جديد.

### هل `count_tokens` مدفوع؟

الـAPI الرسمي يشرح أن `count_tokens` يشغّل tokenizer الخاص بالموديل ويرجع input
count، لكن صفحة الأسعار والـbilling الرسميتين لا تضعان له سعراً منفصلاً بشكل صريح.
لذلك هذا المستند يسجله **$0 كتقدير فقط** ولا يدّعي رسمياً أنه مجاني. قد يظل خاضعاً
للـquota/RPM. لو ظهر بند له في فاتورة Google أو pricing لاحقاً، يوضع سعره في المعادلة:

```text
تكلفة العد = عدد count_tokens requests × سعر الطلب (حالياً placeholder = $0)
```

## الافتراضات القابلة للتعديل — الحالة المتوقعة

لا توجد عينة production طويلة كفاية من usage logs وقت كتابة المستند؛ الأرقام التالية
افتراضات محافظة وليست قياساً فعلياً.

| المتغير | القيمة المتوقعة |
|---|---:|
| أسئلة لكل طالب في اليوم | 5 |
| أيام نشطة في الشهر | 30 |
| نسبة الأسئلة التي تحتاج follow-up rewrite | 60% |
| متوسط rewrite input / output | 800 / 50 tokens |
| متوسط final answer input / output | 6,000 / 500 tokens |
| نسبة الأسئلة التي تشغّل rolling summary | 5% |
| متوسط summary input / output | 5,000 / 500 tokens |
| متوسط query embedding | 50 tokens لكل سؤال |
| محاضرات جديدة شهرياً | 20 |
| متوسط transcript tokens لكل محاضرة | 15,000 |
| transcript ingestion شهرياً | 300,000 embedding tokens |
| retry/error allowance | 5% من تكلفة rewrite + answer + summary |
| long-context price tier | لا يوجد tier إضافي موثق للموديل الحالي |
| re-embedding للمخزون القديم | صفر في الإجمالي؛ يحسب منفصلاً عند تغيير model |

```text
monthly_questions = daily_active_students × 5 × 30
rewrite_calls = monthly_questions × 60%
summary_calls = monthly_questions × 5%
query_embedding_calls = monthly_questions
```

| السيناريو | answer requests | rewrite requests | summary requests | query embeddings |
|---|---:|---:|---:|---:|
| صغير | 15,000 | 9,000 | 750 | 15,000 |
| متوسط | 150,000 | 90,000 | 7,500 | 150,000 |
| كبير | 1,500,000 | 900,000 | 75,000 | 1,500,000 |

## المعادلات

```text
chat_call_cost =
    (input_tokens ÷ 1,000,000 × $0.25)
  + (output_tokens ÷ 1,000,000 × $1.50)

embedding_cost = embedding_tokens ÷ 1,000,000 × $0.20

retry_allowance =
    (rewrite_cost + answer_cost + summary_cost) × retry_percentage

monthly_ai_total =
    rewrite_cost + answer_cost + summary_cost
  + query_embedding_cost + transcript_ingestion_cost
  + retry_allowance + count_tokens_cost + cache_cost
```

## تفصيل الحالة المتوقعة

| السيناريو | Rewrite | Answers | Summaries | Query embedding | Ingestion مرة واحدة للشهر | Retry 5% | الإجمالي USD |
|---|---:|---:|---:|---:|---:|---:|---:|
| صغير | $2.48 | $33.75 | $1.50 | $0.15 | $0.06 | $1.89 | **$39.82** |
| متوسط | $24.75 | $337.50 | $15.00 | $1.50 | $0.06 | $18.86 | **$397.67** |
| كبير | $247.50 | $3,375.00 | $150.00 | $15.00 | $0.06 | $188.63 | **$3,976.19** |

### مثال يدوي بسيط

في السيناريو الصغير:

```text
الأسئلة = 100 × 5 × 30 = 15,000

تكلفة answer واحدة =
    6,000 × $0.25 / 1M + 500 × $1.50 / 1M
  = $0.00225

تكلفة answers = 15,000 × $0.00225 = $33.75

تكلفة rewrites =
    9,000 × (800 × $0.25 / 1M + 50 × $1.50 / 1M)
  = $2.475
```

لتغيير المثال، غيّر عدد الطلاب أو متوسط tokens ثم طبّق نفس المعادلات.

## أفضل/متوقع/استخدام tokens مرتفع

| الحالة | Rewrite % وtokens | Answer input/output | Summary % وtokens | Retry | صغير | متوسط | كبير |
|---|---|---|---|---:|---:|---:|---:|
| Best-case | 40%، 500/30 | 3,000/300 | 2%، 3,000/300 | 2% | $19.98 | $199.24 | $1,991.82 |
| Expected | 60%، 800/50 | 6,000/500 | 5%، 5,000/500 | 5% | $39.82 | $397.67 | $3,976.19 |
| High-token | 80%، 1,500/100 | 11,000/1,000 | 10%، 8,000/800 | 10% | $82.55 | $824.91 | $8,248.56 |

بالجنيه تقريباً، النطاقات هي: صغير **1,005–4,151 EGP**، متوسط
**10,019–41,483 EGP**، وكبير **100,165–414,804 EGP** بسعر الصرف المذكور.

## Embedding وre-embedding

- Query embedding يحصل لكل retrieval وموجود في الإجماليات.
- Transcript embedding يحصل عند ingest للمحاضرات الجديدة: المثال 300,000 token = **$0.06**.
- لو `EMBED_MODEL` اتغير، لازم إعادة embedding لكل المخزون المتأثر:

```text
re_embedding_cost = total_existing_transcript_tokens ÷ 1M × $0.20
```

مثال: مخزون 30 مليون text token يكلف تقريباً **$6 Standard** أو **$3 Batch**،
ولا يدخل هذا المبلغ في الإجماليات لأن حجم المخزون الفعلي غير متوفر هنا.

## Context caching

التطبيق الحالي لا يستخدم Gemini context caching، ولذلك cache cost = صفر. قد يكون
مفيداً فقط عندما نفس lecture context الثابت يُرسل مرات كثيرة، وبعد قياس hit rate.
لا يكفي انخفاض cached-input price وحده؛ توجد تكلفة تخزين بالساعة ويجب مقارنة
التكلفة الفعلية قبل وبعد التفعيل.

## تأثير `CHAT_MAX_INPUT_TOKENS`

رفع product limit يسمح بإضافة transcript evidence أكثر عندما retrieval يجده relevant،
لكنه يرفع input-token cost والـlatency واحتمال دخول pricing tier أطول لو model آخر
يستخدم thresholds. لا يعني أنه سيتم إرسال transcript كامل: candidate count، relevance،
deduplication والـmodel safe limit ما زالوا حدوداً. ابدأ بـ12,000 وراقب p50/p95 usage.

## تكاليف ليست Gemini

لا تدخل في AI API total أعلاه: Supabase/Postgres وpgvector، Bunny Stream، استضافة
FastAPI، network egress، logging/monitoring، تخزين الفيديو والنسخ الاحتياطي.

## توصيات production

1. تسجيل `input_tokens`, `output_tokens`, `total_tokens`, operation وmodel لكل call.
2. عمل dashboard شهري للتكلفة مع p50/p95 لحجم transcript context.
3. alerts عند حدود صرف قابلة للضبط، ومقارنة التقدير بفاتورة provider الفعلية.
4. مراجعة عدد مرات وصول dynamic context لأحجام كبيرة وسبب ذلك.
5. تجربة context caching فقط لنفس lecture context الثابت وبعد حساب hit rate والتخزين.
6. الاحتفاظ بـproduct-level maximum input حتى بدون transcript-only cap.
7. إعادة حساب هذا المستند عند تغيير model أو الأسعار أو سعر الدولار.

> **تنبيه:** الأرقام تقديرية مبنية على افتراضات، وليست عرض سعر أو فاتورة من Google.
