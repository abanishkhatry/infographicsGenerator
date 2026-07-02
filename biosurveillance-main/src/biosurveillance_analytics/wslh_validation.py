"""Prepares validation file for WSLH to validateanalytes with CDC list."""
import pandas as pd
from utils.mapping import tox_values, result_CODED
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles.fills import PatternFill

def get_col_let(colNum):
    """Returns the column letter in an excel sheet
    for the corresponding column index or name."""
    
    return chr(ord('a') + colNum//26 - 1).upper() + chr(ord('a') + colNum%26).upper() if colNum//26 > 0 else chr(ord('a') + colNum).upper()

def _format_data(data):
    """Merges LOINC codes with analyte names from unique LOINC-analyte
     dictionary. Source dictionary is many:1; merging here is done with
     primary analyte name from CDC documentation only, without nickname
     duplicates."""
    
    val_merge = pd.DataFrame({'Analyte_LOINC': tox_values.values(), 'cdc_list': tox_values.keys()}).drop_duplicates(subset='Analyte_LOINC', keep='first')
    detect_merge = pd.DataFrame({'Result_CODED': result_CODED.values(), 'result': result_CODED.keys()})
    data = (data[['analytes', 'Result_CODED']]
        .reset_index()
        .merge(val_merge, on='Analyte_LOINC', how='outer')
        .drop_duplicates()
        .rename(columns={'analytes': 'redcap_matched'})
        .merge(detect_merge, on='Result_CODED', how='left')
        .drop(columns='Result_CODED')
        .sort_values(['Specimen_ID', 'result'])
        .set_index(['Specimen_ID'])
        .fillna(""))
    data = data[~((data['Analyte_LOINC']=="") & (data['redcap_matched']==""))]
    return data

def export_data(data, expPath):
    """Manipulates data and exports with conditional formatting for
      Venn Diagram between CDC list and detected analytes. The goal
      is to facilitate cross-checking matches for WSLH staff to check
      that a) matches are correct, and b) no matches were missed."""
    
    validation_data = _format_data(data)
    
    with pd.ExcelWriter(expPath, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
        validation_data.to_excel(writer, sheet_name='wslh_export', index=False)

        wb  = writer.book
        ws = wb.active
        numRecords = len(validation_data)

        # Export contains one entry for each Specimen_ID, for each 
        # analyte in one of three categories: detected but not on 
        # the CDC list (blue), not detected but on the list (red), 
        # or detected and on the list (purple). 
        ws.conditional_formatting.add('A2:' + get_col_let(len(validation_data.columns)) + str(numRecords + 1), FormulaRule(formula=["AND($" + get_col_let(validation_data.columns.get_loc('cdc_list')) + '2="",NOT($' + get_col_let(validation_data.columns.get_loc('redcap_matched')) + '2=""))'], fill=PatternFill(bgColor='E6B8B7'))) #red
        ws.conditional_formatting.add('A2:' + get_col_let(len(validation_data.columns)) + str(numRecords + 1), FormulaRule(formula=["AND($" + get_col_let(validation_data.columns.get_loc('redcap_matched')) + '2="",NOT($' + get_col_let(validation_data.columns.get_loc('cdc_list')) + '2=""))'], fill=PatternFill(bgColor='B8CCE4'))) #blue
        ws.conditional_formatting.add('A2:' + get_col_let(len(validation_data.columns)) + str(numRecords + 1), FormulaRule(formula=["AND(NOT($" + get_col_let(validation_data.columns.get_loc('redcap_matched')) + '2=""),NOT($' + get_col_let(validation_data.columns.get_loc('cdc_list')) + '2=""))'], fill=PatternFill(bgColor='CCC0DA'))) #purple