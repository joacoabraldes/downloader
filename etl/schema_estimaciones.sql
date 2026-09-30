-- Vista unificada de estimaciones de cultivos: `estimaciones_actual`.
--
-- Una tabla cruda por fuente (grano y metodología distintos) y ESTA vista para consumirlas juntas,
-- como series_actual para las mensuales. Formato LONG, la estimación VIGENTE (sale de las _actual):
--
--   fuente      'magyp_departamental' (estimaciones_agricolas) | 'magyp_semanal' |
--               'magyp_mensual' | 'bcba_pas'
--   informe     publicación en la que apareció el valor (release / informe / "Datos al")
--   fecha_dato  fecha a la que se refiere el valor: fecha de corte del avance (semanal), "Datos al"
--               (BCBA); en las otras dos = informe
--   cultivo     slug normalizado (abajo)      campania  '2025/26'
--   nivel       'pais' | 'provincia' | 'delegacion' | 'departamento' | 'zona_bcba'
--   zona        'total_pais' | slug de provincia/delegación (semanal) | id INDEC del departamento
--               | 'I'..'XV' (zona PAS) | 'oficial_bcba' (ver BCBA abajo)
--   variable / unidad / valor
--
-- Grano único: (fuente, cultivo, campania, nivel, zona, variable).
--
-- NUNCA sumar ni promediar entre fuentes: son metodologías distintas (maíz 2025/26 país: MAGyP
-- mensual 72,5 Mt vs BCBA 63,6 Mt). Se comparan lado a lado, filtrando `fuente`.
--
-- Cultivos (normalización en la vista, las tablas crudas no se tocan):
--   departamental: se saca el sufijo '_total' (soja_total -> soja, trigo_total -> trigo,
--     cebada_total -> cebada, papa_total -> papa, poroto_total -> poroto, cebolla_total ->
--     cebolla). soja_1ra/soja_2da, trigo_candeal, cebada_cervecera/_forrajera quedan como están:
--     son PARTES, no sumarlas con el total.
--   semanal: soja = total, soja_1ra/_2da; trigo = total, trigo_pan/_fideo; arroz, arroz_la/_lf.
--     Se saca un sufijo numérico espurio ('maiz_25': título "MAÍZ 25/26" del informe 30/10/2025).
--   mensual y BCBA: ya vienen como maiz, soja, trigo, girasol, cebada, sorgo, ...
--
-- Variables (unidad):
--   sup_sembrada_ha (ha)          dep sup_sembrada | sem area_sembrada_ha | men
--                                 superficie_implantada_ha | bcba "Sembrado"
--   sup_a_sembrar_ha (ha)         intención: sem area_a_sembrar_ha | men superficie_a_implantar_ha
--   sup_cosechada_ha (ha)         FINAL de la campaña: dep sup_cosechada
--   sup_cosechada_al_dia_ha (ha)  cosechado a la fecha: sem area_cosechada_ha | bcba "Cosechadas"
--   sup_sembrada_al_dia_ha (ha)   sembrado a la fecha: bcba "Sembradas"
--   sup_no_cosechada_ha (ha)      sem area_no_cosechada_ha (incluye silaje/pastoreo en maíz: NO
--                                 es pérdida)
--   sup_perdida_ha (ha)           bcba "Perdído" (pérdida)
--   sup_cosechable_ha (ha)        bcba "Cosechado" = sembrada - perdida
--   sup_grano_ha / sup_silaje_otros_ha (ha)  men, maíz
--   avance_siembra_pct / avance_cosecha_pct (%)  sem avance_pct por fase | bcba
--   produccion_t (t)              dep produccion | men | bcba (ya viene en t aunque el rótulo diga
--                                 "MTn")
--   rendimiento_kg_ha (kg/ha)     dep rendimiento | bcba rinde_qq_ha x 100
--
-- Por fuente:
--   magyp_departamental: nivel 'departamento' (zona = id INDEC) + un agregado 'pais'
--     ('total_pais') = SUMA de departamentos del MISMO cultivo (un solo nivel, sin mezclar total
--     y partes); su rendimiento es DERIVADO = produccion / sup_cosechada * 1000 (no publicado).
--   magyp_semanal: el último corte (fecha_corte) de cada variable. area_sembrada_ha puede venir
--     en la tabla de siembra y en la de cosecha: queda la de corte más reciente.
--   bcba_pas: zona_id 80 -> nivel 'pais', zona 'total_pais' (SUMA de las 15 zonas);
--     zona_id 0 -> nivel 'pais', zona 'oficial_bcba' (la estimación nacional que comunica la
--     BCBA, redondeada, desde 2022/23). Son DOS números nacionales distintos: filtrar zona.
--
-- Tolerante a fuentes faltantes: `etl/initdb.py` la aplica al final y este bloque arma la UNION
-- sólo con las vistas _actual que existen (una base sin algún dataset no rompe init-db). Al sumar
-- el dataset que faltaba, el próximo init-db la recrea completa. Se hace DROP + CREATE (no
-- create or replace): la vista no tiene dependientes y así un cambio de columnas no traba init-db.

do $$
declare
    partes text[] := '{}';
begin
    if to_regclass('etl_estimaciones_agricolas_actual') is not null then
        partes := array_append(partes, $q$
    select 'magyp_departamental'::text as fuente, a.fecha_actualizacion as informe,
           a.fecha_actualizacion as fecha_dato, regexp_replace(a.cultivo, '_total$', '') as cultivo,
           a.campania, 'departamento'::text as nivel, a.departamento_id as zona,
           v.variable, v.valor
    from etl_estimaciones_agricolas_actual a
    cross join lateral (values ('sup_sembrada_ha', a.sup_sembrada),
                               ('sup_cosechada_ha', a.sup_cosechada),
                               ('produccion_t', a.produccion),
                               ('rendimiento_kg_ha', a.rendimiento)) v(variable, valor)
    union all
    select 'magyp_departamental', p.informe, p.informe, p.cultivo, p.campania, 'pais',
           'total_pais', v.variable, v.valor
    from (select regexp_replace(cultivo, '_total$', '') as cultivo, campania,
                 max(fecha_actualizacion) as informe, sum(sup_sembrada) as sembrada,
                 sum(sup_cosechada) as cosechada, sum(produccion) as produccion
          from etl_estimaciones_agricolas_actual
          group by 1, 2) p
    cross join lateral (values ('sup_sembrada_ha', p.sembrada),
                               ('sup_cosechada_ha', p.cosechada),
                               ('produccion_t', p.produccion),
                               ('rendimiento_kg_ha',
                                case when p.cosechada > 0
                                     then p.produccion / p.cosechada * 1000 end)) v(variable, valor)
$q$);
    end if;

    if to_regclass('etl_estimaciones_semanal_actual') is not null then
        partes := array_append(partes, $q$
    select distinct on (cultivo, campania, nivel, zona, variable)
           'magyp_semanal'::text as fuente, informe, fecha_dato, cultivo, campania, nivel, zona,
           variable, valor
    from (select s.date_informe as informe, s.fecha_corte as fecha_dato,
                 regexp_replace(s.cultivo, '_\d+$', '') as cultivo, s.campania,
                 s.zona_tipo as nivel, s.zona,
                 case s.variable
                      when 'avance_pct' then 'avance_' || s.fase || '_pct'
                      when 'area_sembrada_ha' then 'sup_sembrada_ha'
                      when 'area_a_sembrar_ha' then 'sup_a_sembrar_ha'
                      when 'area_cosechada_ha' then 'sup_cosechada_al_dia_ha'
                      when 'area_no_cosechada_ha' then 'sup_no_cosechada_ha'
                      else s.variable end as variable,
                 s.valor, s.ingested_at
          from etl_estimaciones_semanal_actual s) x
    order by cultivo, campania, nivel, zona, variable, fecha_dato desc, informe desc,
             ingested_at desc
$q$);
    end if;

    if to_regclass('etl_estimaciones_mensual_actual') is not null then
        partes := array_append(partes, $q$
    select 'magyp_mensual'::text as fuente, m.date_informe as informe, m.date_informe as fecha_dato,
           m.cultivo, m.campania, 'pais'::text as nivel, 'total_pais'::text as zona,
           case m.variable
                when 'superficie_implantada_ha' then 'sup_sembrada_ha'
                when 'superficie_a_implantar_ha' then 'sup_a_sembrar_ha'
                when 'superficie_grano_ha' then 'sup_grano_ha'
                when 'superficie_silaje_otros_ha' then 'sup_silaje_otros_ha'
                else m.variable end as variable,
           m.valor
    from etl_estimaciones_mensual_actual m
$q$);
    end if;

    if to_regclass('etl_bcba_pas_actual') is not null then
        partes := array_append(partes, $q$
    select 'bcba_pas'::text as fuente, b.fecha_datos as informe, b.fecha_datos as fecha_dato,
           b.cultivo, b.campania,
           case when b.zona_id in (0, 80) then 'pais' else 'zona_bcba' end as nivel,
           case b.zona_id when 80 then 'total_pais' when 0 then 'oficial_bcba'
                else b.zona end as zona,
           case b.variable when 'rinde_qq_ha' then 'rendimiento_kg_ha' else b.variable end
               as variable,
           case b.variable when 'rinde_qq_ha' then b.valor * 100 else b.valor end as valor
    from etl_bcba_pas_actual b
$q$);
    end if;

    execute 'drop view if exists estimaciones_actual';
    if cardinality(partes) = 0 then
        raise notice 'estimaciones_actual: no hay ninguna vista _actual de estimaciones; no se crea';
        return;
    end if;
    execute 'create view estimaciones_actual as
select fuente, informe, fecha_dato, cultivo, campania, nivel, zona, variable,
       case when variable like ''%\_pct'' then ''%''
            when variable like ''%\_kg\_ha'' then ''kg/ha''
            when variable like ''%\_ha'' then ''ha''
            when variable like ''%\_t'' then ''t'' end as unidad,
       valor
from (' || array_to_string(array(select '(' || p || ')' from unnest(partes) p),
                           E'\n    union all\n') || ') u';
end
$$;
