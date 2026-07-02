"""
CDC provides a template with example values. 
This module imports the most recent template and 
clears the data from it.
"""
import os
import pandas as pd
from itertools import product
from utils.mapping import tox_values
from math import nan

class Template:
    def __init__(self, docsDir, uniqSpec):
        self.template = pd.read_csv(os.path.join(docsDir, 'template.csv')).rename(columns=lambda x: x.strip())
        self.template_specimens = uniqSpec #set of unique specimen IDs for this submission
        self.format_template()
    
    def format_template(self):
        """CDC requires reporting for all analytes, regardless of whether they
        are detected or not. This function takes the unique values of result/
        Record ID as indices and columns from the CDC template to create an 
        empty dataframe template."""

        set_analytes = list(set(tox_values.values()))
        set_analytes.append(nan)
        combo = [(*ind[0], ind[1]) for ind in product(self.template_specimens, set_analytes)] # unique patient, specimen, loinc combination
        self.formatted_template = pd.DataFrame(
            index=combo,
            columns=self.template.columns
        ).reset_index(names=['uniqueRows'])
        self.formatted_template[['Patient_ID', 'Specimen_ID', 'Analyte_LOINC']] = pd.DataFrame(self.formatted_template['uniqueRows'].to_list())
        self.formatted_template.drop(columns = 'uniqueRows', inplace=True)
        self.formatted_template = (self.formatted_template
                                   .set_index(['Patient_ID', 'Specimen_ID', 'Analyte_LOINC'])
                                   )
        

        