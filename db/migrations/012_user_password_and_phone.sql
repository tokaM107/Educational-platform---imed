-- Brings an existing database up to the current db/schema.sql.
-- Idempotent: safe to run more than once.
--
--   psql "$DATABASE_URL" -f db/migrations/012_user_password_and_phone.sql

-- What a login checks against: a bcrypt digest, never a password.
--
-- Nullable, because every user that exists today was created without one and
-- there is no honest value to invent for them. NULL means "cannot log in" — the
-- login path must reject it explicitly, because a hash comparison against NULL
-- is not false, it is NULL, and code that tests it loosely can let an empty
-- password through.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;

-- How a student who has forgotten their password proves the account is theirs
-- when the email is exactly what they cannot get into.
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);

-- Ownership of that number, not merely its presence. An unverified phone is a
-- string somebody typed at signup; sending a reset code to it trusts a claim
-- that was never checked.
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMPTZ;

-- E.164 (+201012345678), enforced rather than hoped for.
--
-- The same Egyptian number written 01012345678 and +201012345678 is one person
-- but two values a unique index cannot see as equal, which would let somebody
-- register the spelling the real owner did not and collect their reset codes.
-- Normalising on the way in is what makes the uniqueness below mean anything.
--
-- ADD CONSTRAINT has no IF NOT EXISTS, hence the catalogue check.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_phone_e164'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_phone_e164
            CHECK (phone ~ '^\+[1-9][0-9]{7,14}$');
    END IF;
END $$;

-- One account per number, and the lookup the reset flow runs ("who owns this
-- number?"). NULLs are distinct in a unique index, so users without a phone are
-- untouched by it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users (phone);
