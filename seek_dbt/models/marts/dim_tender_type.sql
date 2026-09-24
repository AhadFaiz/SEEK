select
    row_number() over (order by tender_type_name) as tender_type_key,
    tender_type_name
from {{ ref('stg_tenders_archive') }}
where tender_type_name is not null
group by tender_type_name
