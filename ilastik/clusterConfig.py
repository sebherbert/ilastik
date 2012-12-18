import json
import re

class Namespace(object):
    pass

class AutoEval(object):
    """
    Callable that serves as a pseudo-type.
    Converts a value to a specific type, unless the value is a string, in which case it is evaluated first.
    """
    def __init__(self, t):
        self._t = t
        
    def __call__(self, x):
        if type(x) is self._t:
            return x
        if type(x) is str or type(x) is unicode and self._t is not str:
            return self._t(eval(x))
        return self._t(x)

class FormattedField(object):
    """
    Callable that serves as a pseudo-type for config values that will be used by ilastik as format strings.
    Doesn't actually transform the given value, but does check it for the required format fields.
    """
    def __init__(self, requiredFields, optionalFields=[]):
        assert isinstance(requiredFields, list)
        assert isinstance(optionalFields, list)
        
        self._requiredFields = requiredFields
        self._optionalFields = optionalFields
    
    def __call__(self, x):
        """
        Convert x to str (no unicode), and check it for the required fields.
        """
        x = str(x)
        for f in self._requiredFields:
            fieldRegex = re.compile('{[^}]*' + f +  '}')
            if fieldRegex.search(x) is None:
                raise ConfigSchema.ParsingError( "Format string is missing field: {{{f}}}".format(f=f) )

        # TODO: Also validate that all format fields the user provided are known required/optional fields.
        return x

class ConfigSchema( object ):
    """
    Simple config schema for json config files.
    Currently, only a very small set of json is supported.
    The schema fields must be a single non-nested dictionary of name : type (or pseudo-type) pairs.
    """
    class ParsingError(Exception):
        pass
    
    def __init__(self, fields):
        self._fields = fields
    
    def parseConfigFile(self, configFilePath):
        with open(configFilePath) as configFile:
            try:
                configDict = { str(k) : v for k,v in json.load( configFile ).items() }
            except:
                import sys
                sys.stderr.write( "File '{}' is not valid json.  See stdout for exception details.".format(configFilePath) )
                raise

            try:
                return self._getNamespace(configDict)
            except ConfigSchema.ParsingError, e:
                raise ConfigSchema.ParsingError( "Error parsing config file '{f}':\n{msg}".format( f=configFilePath, msg=e.args[0] ) )

    def _getNamespace(self, configDict):
        namespace = Namespace()
        # All config fields are None by default
        for key in self._fields.keys():
            setattr(namespace, key, None)
        
        # Keys that the user gave us are 
        for key, value in configDict.items():
            if key in self._fields.keys():
                fieldType = self._fields[key]
                try:
                    finalValue = self._transformValue( fieldType, value )
                except ConfigSchema.ParsingError, e:
                    raise ConfigSchema.ParsingError( "Error parsing config field '{f}':\n{msg}".format( f=key, msg=e.args[0] ) )
                else:
                    setattr( namespace, key, finalValue )
        return namespace
    
    def _transformValue(self, fieldType, val):
        # Check special error cases
        if fieldType is bool and not isinstance(val, bool):
            raise ConfigSchema.ParsingError( "Expected bool, got {}".format( type(val) ) )
        
        # Other special types will error check when they construct.
        return fieldType( val )
            
#: Schema for all cluster config options
#: (Doesn't specify which are required and which aren't.)
ClusterConfigFields = \
{
    "workflow_type" : str,
    "sys_tmp_dir" : str,
    "scratch_directory" : str,
    "num_jobs" : AutoEval(int),
    "task_timeout_secs" : AutoEval(int),
    "use_node_local_scratch" : bool,
    "use_master_local_scratch" : bool,
    "task_progress_update_command" : FormattedField( requiredFields=["progress"] ),
    "task_launch_server" : str,
    "server_working_directory" : str,
    "command_format" : FormattedField( requiredFields=["task_args"], optionalFields=["task_name"] )
}

def parseClusterConfigFile( configFilePath ):
    """
    Convenience function for parsing cluster configs.
    Returns a Namespace object.
    (Similar to the behavior of argparse.ArgumentParser.parse_args() )
    """
    schema = ConfigSchema( ClusterConfigFields )
    return schema.parseConfigFile( configFilePath )

if __name__ == "__main__":
    testConfig = """
{
    "workflow_type" : "PixelClassificationWorkflow",
    "sys_tmp_dir" : "/scratch/bergs",
    "scratch_directory" : "/home/bergs/clusterstuff/scratch",
    "num_jobs" : "2**3",
    "task_timeout_secs" : "20*60",
    "use_node_local_scratch" : true,
    "use_master_local_scratch" : true,
    "task_progress_update_command" : "./update_job_name {progress}",
    "command_format" : "qsub -pe batch 4 -l short=true -N {task_name} -j y -b y -cwd -V '/groups/flyem/proj/builds/cluster/src/ilastik-HEAD/ilastik_clusterized {args}'"
}
"""
    # Create a temporary file
    import tempfile
    fname = tempfile.mktemp()
    with file(fname, 'w') as f:
        f.write(testConfig)
    
    config = parseClusterConfigFile(fname)
    assert config.workflow_type == "PixelClassificationWorkflow"
    assert isinstance(config.workflow_type, str)
    assert config.use_node_local_scratch is True
    assert config.task_timeout_secs == 20*60


    
    