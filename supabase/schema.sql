-- Controle mensuel : tables alimentees par `python -m socialstats.publish`.
-- A executer une fois dans Supabase > SQL Editor. Le script peut etre rejoue
-- sans risque : il ne supprime aucune donnee.
--
-- Principe d'acces, avec la cle publique (celle du site) :
--   - lire les resultats publies et le suivi des imports ;
--   - deposer un export CSV dans le dossier `imports/` du depot `exports`,
--     et le declarer dans la table `imports`. Rien d'autre : ni lecture ni
--     remplacement ni suppression d'un fichier, ni modification d'un resultat.
-- Seule la cle secrete, qui ignore ces regles, ecrit les resultats ; elle sert
-- a la publication, sur le poste local ou dans GitHub Actions.

-- Historique des publications organiques retenues par le nettoyage.
create table if not exists public.publications (
  post_id      text primary key,
  compte       text not null,
  client       text,
  secteur      text,
  plateforme   text not null,
  format       text not null,
  publie_le    timestamp not null,
  mois         text not null,
  vues         double precision,
  couverture   double precision,
  interactions double precision,
  engagement   double precision,
  lien         text,
  texte        text,
  fichier      text,
  maj_le       timestamptz not null
);

create index if not exists publications_groupe_idx
  on public.publications (compte, plateforme, format, mois);

-- Une reponse precalculee par mois controle et par periode de reference,
-- au format de l'API locale /api/controle.
create table if not exists public.controles (
  mois    text not null,
  ref     text not null,
  payload jsonb not null,
  maj_le  timestamptz not null,
  primary key (mois, ref)
);

-- Etat de la derniere publication : mois disponibles, references, date.
create table if not exists public.etat (
  cle    text primary key,
  valeur jsonb not null,
  maj_le timestamptz not null
);

alter table public.publications enable row level security;
alter table public.controles enable row level security;
alter table public.etat enable row level security;

drop policy if exists "lecture publique" on public.publications;
create policy "lecture publique" on public.publications
  for select to anon, authenticated using (true);

drop policy if exists "lecture publique" on public.controles;
create policy "lecture publique" on public.controles
  for select to anon, authenticated using (true);

drop policy if exists "lecture publique" on public.etat;
create policy "lecture publique" on public.etat
  for select to anon, authenticated using (true);

revoke all on public.publications, public.controles, public.etat from anon, authenticated;
grant select on public.publications, public.controles, public.etat to anon, authenticated;
grant all on public.publications, public.controles, public.etat to service_role;

-- Depot prive des exports Meta bruts : 50 Mo et CSV au plus par fichier.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('exports', 'exports', false, 52428800, array['text/csv'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- Le site depose sans connexion, dans `imports/` uniquement, et sans pouvoir
-- remplacer un fichier existant (aucun droit de lecture ni de mise a jour).
drop policy if exists "depot des exports depuis le site" on storage.objects;
create policy "depot des exports depuis le site" on storage.objects
  for insert to anon, authenticated
  with check (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = 'imports'
    and lower(storage.extension(name)) = 'csv'
  );

-- Suivi des exports deposes sur le site. La publication renseigne le statut :
-- ajoute, deja_charge (contenu deja present) ou refuse (format non reconnu).
create table if not exists public.imports (
  id           uuid primary key default gen_random_uuid(),
  nom          text not null check (char_length(nom) between 1 and 200),
  chemin       text not null unique check (chemin ~ '^imports/[A-Za-z0-9._-]{1,150}[.]csv$'),
  taille       bigint check (taille between 1 and 52428800),
  statut       text not null default 'en_attente'
               check (statut in ('en_attente', 'en_cours', 'ajoute', 'deja_charge', 'refuse')),
  detail       text,
  publications integer,
  plateforme   text,
  comptes      integer,
  debut        date,
  fin          date,
  depose_le    timestamptz not null default now(),
  traite_le    timestamptz
);

create index if not exists imports_statut_idx on public.imports (statut, depose_le);

alter table public.imports enable row level security;

drop policy if exists "lecture publique" on public.imports;
create policy "lecture publique" on public.imports
  for select to anon, authenticated using (true);

drop policy if exists "depot depuis le site" on public.imports;
create policy "depot depuis le site" on public.imports
  for insert to anon, authenticated
  with check (statut = 'en_attente' and traite_le is null);

revoke all on public.imports from anon, authenticated;
grant select on public.imports to anon, authenticated;
-- Le site ne renseigne que ces trois colonnes ; le reste prend sa valeur par defaut.
grant insert (nom, chemin, taille) on public.imports to anon, authenticated;
grant all on public.imports to service_role;

-- Declenchement du calcul : chaque depot demande a GitHub Actions de lancer
-- la publication. Sans jeton enregistre, rien n'est envoye et la verification
-- horaire du workflow prend le relais.
--
-- Pour activer le declenchement immediat, une seule fois :
--   select vault.create_secret('<jeton GitHub>', 'github_token');
-- (jeton "fine-grained" limite au depot, permission Actions : lecture et ecriture)
create extension if not exists pg_net with schema extensions;

create or replace function public.lancer_publication()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  jeton text;
begin
  select decrypted_secret into jeton
  from vault.decrypted_secrets
  where name = 'github_token'
  limit 1;

  if jeton is null then
    return null;
  end if;

  perform net.http_post(
    url := 'https://api.github.com/repos/Skwiz-blip/verification/actions/workflows/publier.yml/dispatches',
    body := jsonb_build_object('ref', 'main'),
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || jeton,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'controle-mensuel',
      'Content-Type', 'application/json'
    )
  );
  return null;
end;
$$;

revoke all on function public.lancer_publication() from public, anon, authenticated;

drop trigger if exists lancer_publication on public.imports;
create trigger lancer_publication
  after insert on public.imports
  for each statement
  execute function public.lancer_publication();
