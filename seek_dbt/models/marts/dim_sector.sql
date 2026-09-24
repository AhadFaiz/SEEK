select
    row_number() over (order by sector) as sector_key,
    sector
from {{ ref('stg_tenders_archive') }}
where sector is not null
group by sector
