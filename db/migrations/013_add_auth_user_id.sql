ALTER TABLE public.users
ADD COLUMN auth_user_id UUID UNIQUE;

ALTER TABLE public.users
ADD CONSTRAINT users_auth_user_id_fkey
FOREIGN KEY (auth_user_id)
REFERENCES auth.users(id)
ON DELETE SET NULL;