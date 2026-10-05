-- Controle mensuel : tables alimentees par `python -m socialstats.publish`.
-- A executer une fois dans Supabase > SQL Editor. Le script peut etre rejoue
-- sans risque : il ne supprime aucune donnee.
--
-- Principe d'acces : la cle publique LIT les trois tables, rien de plus.
-- Aucune regle n'autorise l'ecriture : seule la cle secrete, qui ignore ces
-- regles, peut modifier les donnees. Les exports bruts restent dans un depot
-- prive, illisible avec la cle publique.

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

-- Archive privee des exports Meta bruts.
insert into storage.buckets (id, name, public)
values ('exports', 'exports', false)
on conflict (id) do nothing;
