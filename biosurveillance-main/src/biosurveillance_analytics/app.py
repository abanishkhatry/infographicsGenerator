"""Main module."""
import os
from config import Config
import pandas as pd
from utils.form import Form
from utils.template import Template
from utils.mapping import default_vals, test_not_done, result_CODED
from validation import final_schema
from wslh_validation import export_data
from datetime import date, timedelta
from s4_config import api_url, api_token
import requests

def _raw(dataDir):
    """Returns the raw REDCap data."""
    rawData = pd.read_csv(
        os.path.join(dataDir, 'raw.csv')
    ).astype(str)
    
    return rawData

def _pull_redcap_data(dataDir) -> pd.DataFrame:
    """Fetch a REDCap report via the API and return as pandas DataFrame."""
    data = {
    'token': api_token,
    'content': 'record',
    'action': 'export',
    'format': 'json',
    'type': 'flat',
    'csvDelimiter': '',
    'rawOrLabel': 'label',
    'rawOrLabelHeaders': 'label',
    'exportCheckboxLabel': 'true',
    'exportSurveyFields': 'true',
    'exportDataAccessGroups': 'false',
    'returnFormat': 'json',
    'dateRangeBegin': ''
    }

    s = requests.Session()
    response = s.post(api_url, data=data)
    print('HTTP Status: ' + str(response.status_code))
    rawData = pd.DataFrame.from_records(response.json())
    print(rawData)
    rawData = rawData.replace({"": None})
    rawData.to_csv(
        os.path.join(dataDir, 'raw.csv')
    ).astype(str)
    
    return rawData

def _transform(data):
    """Transforms spec and qtof form data."""    
    
    return Form(data)

def _template(docsDir, uniqSpec):
    """Formats the empty template for submission.
     Documentation is provided by CDC, including a
     technical guidance document, and template read
     by the template module."""

    return Template(docsDir, uniqSpec)

def _merge_ids(form_object, temp_object):
    """Merges records from qtof and specimen forms on their patient ids."""

    spc = form_object.spec.transformed_data
    qtf = form_object.qtof.transformed_data
    tmp = temp_object.formatted_template
    for_validation = qtf.combine_first(tmp)
    for_validation[['Test_dt', 'Received_dt', 'Sample_Type']] = for_validation.groupby(level=['Specimen_ID'])[['Test_dt', 'Received_dt', 'Sample_Type']].ffill().bfill()
    for_validation[['Test_dt', 'Received_dt']] = for_validation.groupby(level=['Patient_ID'])[['Test_dt', 'Received_dt']].ffill().bfill()
    merged = spc.combine_first(for_validation)
    merged[['Complete?', 'Complete?.1']] = merged.groupby(level=['Specimen_ID'])[['Complete?', 'Complete?.1']].ffill().bfill()
    merged = merged[(merged['Complete?']=='Complete') & (merged['Complete?.1']=='Complete')]
    merged = merged.loc[merged.index.dropna()]

    return merged, for_validation

def _fill_defaults(data):
    """Some fields are either not required for data submission
     or have a constant value for all indices. These fields are
     filled with stored values."""
    
    for key, val in default_vals.items():
        data[key] = data[key].fillna(val)

    data.loc[data.index.get_level_values(1).isin(test_not_done), 'Result_CODED'] = result_CODED['Test not done'] #Overwrite "not detected" for analytes that were not tested

    return data

def _validate(data):
    """Pandera-based validation step validates data types and accepted
     values."""
    
    return final_schema.validate(data)

def _wslh_export(validation_data, dataDir):
    """WSLH staff should check each specimen for detected, not detected, etc. analytes."""
    
    expPath = os.path.join(dataDir, 'wslh_export.xlsx')
    if not os.path.exists(expPath):
        pd.DataFrame().to_excel(expPath)

    export_data(validation_data, expPath)

def main(*args):
    activeQuarter = ((date.today().replace(day=1) - timedelta(days=1)).month - 1) // 3 + 1 #get reporting quarter (i.e., quarter last month)
    q_string = (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y - ' + 'Q' + str(activeQuarter))  #e.g., 2025 - Q4
    q_string = "2026 - Q1"

    projDir = Config.config("project_dir")
    dataDir = Config.set("data_dir", os.path.join(projDir, 'data\\' + q_string))
    docsDir = Config.set("docs_dir", os.path.join(projDir, 'docs')) 

    # data = _pull_redcap_data(dataDir)
    data = _raw(dataDir)
    form_obj = _transform(data)
    temp_obj = _template(docsDir, form_obj.uniqSpec)
    merged, for_validation = _merge_ids(form_obj, temp_obj)
    final = _fill_defaults(merged)
    final = final.reset_index()[temp_obj.template.columns]
    
    # _validate(final.reset_index())

    if 'cdc' in args:   
        maxRecords = int(args[-1])
        final.reset_index().to_excel(os.path.join(dataDir, 'cdc_output.xlsx'), index=False) 
        for ix, iChunk in enumerate(range(0, len(final), maxRecords)):
            if len(final) - iChunk > maxRecords:
                final.iloc[iChunk: iChunk + maxRecords].to_csv(os.path.join(dataDir, 'cdc_output_' + str(ix) + '.csv'), index=False)
            else:
                final.iloc[iChunk:].to_csv(os.path.join(dataDir, 'cdc_output_' + str(ix) + '.csv'), index=False)

    if 'wslh' in args:    
        _wslh_export(for_validation, dataDir) 
    