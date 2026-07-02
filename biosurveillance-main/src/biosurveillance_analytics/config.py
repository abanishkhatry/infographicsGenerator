import os

class Config:
    _conf = {
        "project_dir": os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data_dir": None,
        'docs_dir': None
    }

    _setters = [
        'project_dir',
        'data_dir',
        'docs_dir'
    ]

    @staticmethod
    def config(name):
        return Config._conf[name]
    
    @staticmethod
    def set(name, value):
        if name in Config._setters:
            Config._conf[name] = value
            return value
        else:
            raise NameError()
    
    @staticmethod
    def get(name):
        return Config._conf[name]