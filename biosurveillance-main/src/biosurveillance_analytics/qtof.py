"""Transforms toxicology results variables."""
from utils.mapping import tox_values, result_CODED
from thefuzz import process

def match_accepted(detected):
    matches = {}
    for an in tox_values.keys():
        match = process.extract(an, detected)
        for m in match:
            if m[1]==100:
                matches[m[0]] = an
            if m[1] > 90 and m[0] not in matches:
                    matches[m[0]] = an
    return matches

def test_dates():
    pass

class Qtof:
    """Creates a qtof form object and houses its distinct transformations."""
    def __init__(self, data):
        self.raw_data = data
        self.data = data
        self._transform()

    def _format(self):        
        self.data.loc[:, 'Date of sample prep & QToF data acquisition'] = self.data.loc[:, 'Date of sample prep & QToF data acquisition'] + " 00:00"
        self.data.loc[:, 'Date received by WSLH'] = self.data.loc[:, 'Date received by WSLH'] + " 00:00"
        self.data.loc[:, 'Sample matrix'] = self.data.loc[:, 'Sample matrix'].str.title()

    def _get_unique(self):
        """Homogenizes manually-entered REDCap results. Returns Series of unique analytes in dataset."""
        self.data.loc[:, 'analytes'] = (self.data.loc[:, 'Positive Ion Mode'] + ',' + self.data.loc[:, 'Negative Ion Mode']).str.upper().str.split(',')
        self.data = self.data.explode('analytes')
        self.data['analytes'] = self.data['analytes'].str.strip()
        self.unique_analytes = self.data['analytes'].unique()

    def _get_matches(self):
        self.matches = match_accepted(self.unique_analytes)
        self.data['match'] = self.data['analytes'].map(self.matches).fillna('')
        self.data.loc[self.data['match'] != '', 'result'] = 'Detected'
        # self.data = self.data[self.data['analytes'].isin(list(self.matches.keys()))] #only keep the ones that actually matched; will populate Not Detected on merge

    def _replace_values(self):
        """Replaces values with accepted values from CDC technical guidance."""
        self.data['result'] = self.data['result'].map(result_CODED)
        self.data['loinc'] = self.data['match'].map(tox_values)

    def _transform(self):
        """All the functions that need to be done to qtof data."""
        self._format()
        self._get_unique()
        self._get_matches()
        self._replace_values()
        self.transformed_data = self.data.drop_duplicates()
