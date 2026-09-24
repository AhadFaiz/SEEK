select
    row_number() over (order by source_entity) as agency_key,
    source_entity
from {{ ref('stg_tenders_archive') }}
where source_entity is not null
group by source_entity
