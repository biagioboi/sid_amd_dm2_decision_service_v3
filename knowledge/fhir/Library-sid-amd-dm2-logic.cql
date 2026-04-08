library SIDAMDDM2Logic version '1.0.0'

parameter HbA1cPercent Decimal
parameter EGFR Decimal
parameter PriorCardiovascularEvent Boolean
parameter HeartFailure Boolean
parameter OnHypoglycemiaInducingTherapy Boolean
parameter StructuredEducationDone Boolean
parameter GroupEducationFeasible Boolean
parameter StructuredNutritionProgram Boolean
parameter RegularPhysicalActivity Boolean
parameter OnBasalBolus Boolean
parameter NeedsBasalInsulin Boolean
parameter NeedsPrandialInsulin Boolean
parameter OnPump Boolean
parameter MetforminContraindicated Boolean
parameter SGLT2iContraindicated Boolean
parameter GLP1RAContraindicated Boolean
parameter PoorlyControlled Boolean

define NeedsTargetEvaluation:
  HbA1cPercent is not null

define TargetUpperBound:
  if OnHypoglycemiaInducingTherapy then 7.5
  else 7.0

define TargetRecommendationText:
  if OnHypoglycemiaInducingTherapy
    then 'Target HbA1c 6.6%–7.5% (SID/AMD 1.1)'
    else 'Target HbA1c <7.0% (SID/AMD 1.2.1; valutare <6.5% se appropriato)'

define Phenotype:
  if HeartFailure then 'HF'
  else if PriorCardiovascularEvent then 'CVD_NO_HF'
  else if EGFR < 60 then 'CKD_NO_CVD_NO_HF'
  else 'STANDARD'

define FirstLineClasses:
  case
    when Phenotype = 'HF' then { 'sglt2i' }
    when Phenotype = 'CVD_NO_HF' then { 'metformin', 'sglt2i', 'glp1ra' }
    when Phenotype = 'CKD_NO_CVD_NO_HF' then { 'metformin', 'sglt2i' }
    else { 'metformin' }
  end

define NeedsLifestyleIntervention:
  (not StructuredNutritionProgram) or (not RegularPhysicalActivity) or (not StructuredEducationDone)

define NeedsPharmacologicEvaluation:
  HbA1cPercent is not null

define NeedsMonitoringPlan:
  true
