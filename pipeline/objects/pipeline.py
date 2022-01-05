from pipeline.objects.graph import Graph
from pipeline.objects.variable import Variable
from pipeline.objects.function import Function
from pipeline.objects.graph_node import GraphNode


class Pipeline:
    defined_pipelines = {}

    _current_pipeline: Graph
    _pipeline_context_active: bool = False
    _pipeline_context_name: str = None

    def __init__(self, new_pipeline_name):
        self._pipeline_context_name = new_pipeline_name

    def __enter__(self):
        Pipeline._pipeline_context_active = True

        Pipeline._current_pipeline = Graph(name=self._pipeline_context_name)

        return self

    def __exit__(self, type, value, traceback):
        Pipeline.defined_pipelines[
            Pipeline._current_pipeline.name
        ] = Pipeline._current_pipeline
        Pipeline._pipeline_context_active = False
        Pipeline._current_pipeline = None

    def output(self, *outputs: Variable) -> None:
        for _output in outputs:
            variable_index = Pipeline._current_pipeline.variables.index(_output)
            if variable_index != -1:
                Pipeline._current_pipeline.variables[variable_index].is_output = True

                Pipeline._current_pipeline.outputs.append(
                    Pipeline._current_pipeline.variables[variable_index]
                )
                for variable in Pipeline._current_pipeline.variables:
                    if variable.local_id == _output.local_id:
                        variable.is_output = True
                        break

    @staticmethod
    def get_pipeline(graph_name: str) -> Graph:
        if graph_name in Pipeline.defined_pipelines:
            return Pipeline.defined_pipelines[graph_name]
        else:
            raise Exception("No Pipeline graph found with name '%s'" % graph_name)

    @staticmethod
    def add_variable(variable: Variable) -> None:
        if Pipeline._pipeline_context_active:

            if variable not in Pipeline._current_pipeline.variables:
                Pipeline._current_pipeline.variables.append(variable)
        else:
            raise Exception("Cant add a variable when not defining a pipeline!")

    @staticmethod
    def add_function(function: Function) -> None:
        if Pipeline._pipeline_context_active:
            Pipeline._current_pipeline.functions.append(function)
        else:
            raise Exception("Cant add a function when not defining a pipeline!")

    @staticmethod
    def add_graph_node(graph_node: GraphNode) -> None:
        if Pipeline._pipeline_context_active:
            Pipeline._current_pipeline.nodes.append(graph_node)
        else:
            raise Exception("Cant add a node when not defining a pipeline!")
