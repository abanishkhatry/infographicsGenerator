from utils.mapping import shared_cols, form_props, var_names
from qtof import Qtof
from spec import Spec

class Form:
    def __init__(self, data):       
        self.uniqSpec = list(data[shared_cols + ['Specimen number']].drop_duplicates().to_records(index=False))
        self.raw_data = (data
                        .set_index(shared_cols)
                        .replace({
                             "Unchecked": False,
                             "Checked": True}))
        self.transform()
        
    def transform(self):
        self.spec = Spec(self.raw_data.loc[self.raw_data['Repeat Instrument']==form_props['spec']['filt'], form_props['spec']['cols']])
        self.qtof = Qtof(self.raw_data.loc[self.raw_data['Repeat Instrument']==form_props['qtof']['filt'], form_props['qtof']['cols']].set_index(['Specimen number'], append=True))

        self.spec.transformed_data = self.spec.transformed_data.reset_index().rename(columns=var_names).set_index(['Patient_ID'])
        self.qtof.transformed_data = self.qtof.transformed_data.reset_index().rename(columns=var_names).set_index(['Patient_ID', 'Specimen_ID', 'Analyte_LOINC'])



