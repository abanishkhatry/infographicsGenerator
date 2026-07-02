"""Data validation methods for export."""
import pandera.pandas as pa
from utils.mapping import tox_values, demographic_cols

final_schema = pa.DataFrameSchema(
    columns={
        "Activity_Type": pa.Column(
            str,
            pa.Check.isin(['OD2A'])
            ),
        "Specimen_ID": pa.Column(str),
        "Patient_ID": pa.Column(str),
        "Sample_Type": pa.Column(
            str,
            pa.Check.isin([
                'Blood',
                'Plasma',
                'Serum',
                'Urine'
            ])
        ),
        "Received_dt": pa.Column(),
        "Collection_dt": pa.Column(),
        "Sample_ID": pa.Column(str),
        "Run_ID": pa.Column(str),
        "Run_Create_dt": pa.Column(str),
        "Analyte_LOINC": pa.Column(
            str,
            pa.Check.isin(
                list(tox_values.values()))
        ),
        "Test_dt": pa.Column(),
        "Result_CODED": pa.Column(
            str,
            pa.Check.isin([
                '260373001',
                '260415000',
                '280414007',
                '419984006',
                '373121007',
            ])
        ),
        "Result_NUMERIC": pa.Column(str),
        "Unit_Of_Measure": pa.Column(str),
        "Analyte_Result_Comment": pa.Column(str),
        "Instrument_Type": pa.Column(
            str,
            pa.Check.isin([
                'LC/MS/MS',
                'ICP/MS',
                'GC/MSD',
                'LC/ICP/MS',
                'GC/MS/MS',
                'LC/HRMS',
            ])
        ),
        "Patient_State": pa.Column(
            str,
            pa.Check.isin([
                '',
                'AL',
                'AK',
                'AZ',
                'AR',
                'CA',
                'CO',
                'CT',
                'DC',
                'DE',
                'FL',
                'GA',
                'HI',
                'ID',
                'IL',
                'IN',
                'IA',
                'KS',
                'KY',
                'LA',
                'ME',
                'MD',
                'MA',
                'MI',
                'MN',
                'MS',
                'MO',
                'MT',
                'NE',
                'NV',
                'NH',
                'NJ',
                'NM',
                'NY',
                'NC',
                'ND',
                'OH',
                'OK',
                'OR',
                'PA',
                'PR',
                'RI',
                'SC',
                'SD',
                'TN',
                'TX',
                'UT',
                'VT',
                'VA',
                'WA',
                'WV',
                'WI',
                'WY',
            ])
        ),
        "Patient_County": pa.Column(str),
        "Patient_Zip": pa.Column(str),
        "Patient_DOB": pa.Column(),
        "Patient_Age": pa.Column(str),
        "Age_Unit": pa.Column(
            str,
            pa.Check.isin([
                'Hour',
                'Day',
                'Month',
                'Year'
            ])
        ),
        "Patient_Sex": pa.Column(
            str,
            pa.Check.isin([
                'M',
                'F',
            ])
        ),
        "Patient_Race": pa.Column(
            str, 
            pa.Check.str_matches(r'({})(~({}))*$'.format('|'.join(demographic_cols['race'].values()), '|'.join(demographic_cols['race'].values())))
        ),
        "Patient_Ethnicity": pa.Column(
            str,
            pa.Check.str_matches(r'({})(~({}))*$'.format('|'.join(demographic_cols['ethnicity'].values()), '|'.join(demographic_cols['ethnicity'].values())))
        ),
        "Analyst": pa.Column(str),
        "Reviewing_Official": pa.Column(str),
    },
    # index=pa.Index(int),
    strict=True,
    # coerce=True,
)