select
    t.tender_id,
    t.reference_number,
    a.agency_key,
    s.sector_key,
    tt.tender_type_key,
    d.date_key,
    t.buying_cost,
    t.financial_fees,
    t.invitation_cost
from {{ ref('stg_tenders_archive') }} t
left join {{ ref('dim_agency') }} a on t.source_entity = a.source_entity
left join {{ ref('dim_sector') }} s on t.sector = s.sector
left join {{ ref('dim_tender_type') }} tt on t.tender_type_name = tt.tender_type_name
left join {{ ref('dim_date') }} d on cast(t.last_offer_presentation_date as date) = d.full_date
