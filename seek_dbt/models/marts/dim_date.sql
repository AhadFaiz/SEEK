select
    row_number() over (order by full_date) as date_key,
    full_date,
    extract(year from full_date) as year,
    extract(month from full_date) as month,
    extract(quarter from full_date) as quarter
from (
    select distinct cast(last_offer_presentation_date as date) as full_date
    from {{ ref('stg_tenders_archive') }}
    where last_offer_presentation_date is not null
)
