"""Transforms patient data variables."""
from utils.mapping import demographic_cols
import re

class Spec:
    """Creates a spec form object and houses its distinct
     transformations."""

    def __init__(self, data):
        self.raw_data = data
        self.data = data
        self._transform()

    def _format(self):
        self.data["Patient's age"] = self.data["Patient's age"].astype(float).astype(int).astype(str)

    def _pivot_re(self, demographic):
        """Pivots column-named race/ethnicity values into 
        singular column for given demographic variable."""
        rel_cols = self.data[list(demographic_cols[demographic].keys())]
        is_max = rel_cols.eq(rel_cols.max(axis=1), axis=0)
        self.data[demographic] = is_max.dot(rel_cols.columns + '~').str[:-1]
        self.data = self.data.drop(list(demographic_cols[demographic].keys()), axis=1)
    
    def _replace_values(self):
        """Replaces values with accepted values from CDC
          technical guidance."""
        for key in demographic_cols.keys():
            for k, v in demographic_cols[key].items():
                self.data[key] = self.data[key].replace(re.escape(k), v, regex=True)

    def _transform(self):
        """All the functions that need to be done to spec
          data."""
        self._format()
        self._pivot_re('race')
        self._pivot_re('ethnicity')
        self._replace_values()
        self.transformed_data = self.data
