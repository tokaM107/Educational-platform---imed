import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

import psycopg
from pgvector.psycopg import register_vector
from pgvector import Vector


load_dotenv()

# -------------------------
# 1. Gemini client
# -------------------------

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


# -------------------------
# 2. PostgreSQL connection
# -------------------------

conn = psycopg.connect(
    os.getenv("DATABASE_URL")
)

register_vector(conn)


# -------------------------
# 3. Create an embedding
# -------------------------

text = "Hypertension هو السبب الرئيسي لل stroke."

result = client.models.embed_content(
    model="gemini-embedding-2",
    contents=text,
    config=types.EmbedContentConfig(
        output_dimensionality=1536
    )
)
embedding = Vector(result.embeddings[0].values)

# print("Embedding length:", len(embedding))


# -------------------------
# 4. Store it in database
# -------------------------

with conn.cursor() as cur:

    cur.execute(
        """
        INSERT INTO transcript_chunks
        (lecture_id, text, start_ts, end_ts, embedding)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            1,
            text,
            0,
            10,
            embedding
        )
    )

    conn.commit()

print("Embedding stored successfully!")


# -------------------------
# 5. Create embedding for a query
# -------------------------

query = "ايه اكتر حاجة بتعلي خطر الاصابة بالاستروك"

query_result = client.models.embed_content(
    model="gemini-embedding-2",
    contents=query,
    config=types.EmbedContentConfig(
        output_dimensionality=1536
    )
)

query_embedding = Vector(query_result.embeddings[0].values)


# -------------------------
# 6. Similarity search
# -------------------------

with conn.cursor() as cur:

    cur.execute(
        """
        SELECT
            id,
            text,
            start_ts,
            end_ts,
            embedding <=> %s AS distance
        FROM transcript_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s
        LIMIT 3
        """,
        (query_embedding, query_embedding)
    )

    results = cur.fetchall()


# -------------------------
# 7. Print results
# -------------------------

print("\nSearch results:")

for row in results:
    print(row)


conn.close()