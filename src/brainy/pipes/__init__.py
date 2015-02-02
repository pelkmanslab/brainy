'''
iBRAINPipes is an integration of pipette processes into iBRAIN modules.
'''
from __future__ import with_statement
import os
import shutil
import pipette
import logging
from brainy.process import BrainyProcessError
from brainy.flags import FlagManager
from brainy.utils import Timer
from brainy.project.report import BrainyReporter, report_data
logger = logging.getLogger(__name__)


PROCESS_NAMESPACE = 'brainy.pipes'


class BrainyPipeFailure(Exception):
    '''Thrown when pipeline execution has to be interrupted.'''


class ProccessEndedIncomplete(BrainyPipeFailure):
    '''One of the pipe's processes failed to complete successfully.'''


class BrainyPipe(pipette.Pipe):

    def __init__(self, pipes_manager, definition=None):
        super(BrainyPipe, self).__init__(PROCESS_NAMESPACE, definition)
        self.pipes_manager = pipes_manager
        self.has_failed = False
        self.previous_process_params = None

    @property
    def pipe_extension(self):
        return self.pipes_manager.pipe_extension

    @property
    def output_path(self):
        return os.path.join(self.pipes_manager.project_path, self.name)

    def instantiate_process(self, process_description,
                            default_type=None):
        process = super(BrainyPipe, self).instantiate_process(
            process_description, default_type)
        process.name_prefix = self.name
        return process

    def get_step_name(self, process_name):
        return '%s-%s' % (self.name, process_name)

    def get_previous_parameters(self):
        if self.previous_process_params is None:
            return
        if not self.previous_process_params['previous_process_params'] is None:
            # Avoid chaining the back up to the first process. Such linking
            # can motivate a very bad programming practices. Only one step
            # before is allowed to memorize. Everything else is just to
            # complicated. So we unlink previous of previous here.
            self.previous_process_params['previous_process_params'] = None
        return self.previous_process_params

    def execute_process(self, process, parameters):
        '''
        Execute process as a step in brainy pipeline. Add verbosity, e.g.
        report status using brainy project report scheme.
        '''
        step_name = self.get_step_name(process.name)
        logger.info('Executing step {%s}' % step_name)
        parameters['pipes_manager'] = self.pipes_manager
        parameters['process_path'] = os.path.join(
            self.pipes_manager._get_flag_prefix(),
            self.name,
        )
        parameters['step_name'] = step_name
        # Some modules are allowed to have limited dependency on previous
        # steps, but this is restricted. Also check unlinking in
        # get_previous_parameters().
        parameters['previous_process_params'] = self.get_previous_parameters()
        self.previous_process_params = parameters

        BrainyReporter.append_report_process(process.name)

        try:
            super(BrainyPipe, self).execute_process(process, parameters)

            if not process.is_complete:
                raise ProccessEndedIncomplete()

        except BrainyProcessError as error:
            # See brainy.errors
            error_is_fatal = False
            if 'message_type' in error.extra:
                if error.extra['message_type'] == 'error':
                    error_is_fatal = True
                del error.extra['message_type']
            if 'error_type' in error.extra:
                BrainyReporter.append_known_error(
                    message=str(error),
                    **error.extra)
            else:
                BrainyReporter.append_unknown_error(
                    message=str(error),
                    **error.extra)
            # Finally, interrupt execution if we error is fatal (default).
            if error_is_fatal:
                raise BrainyPipeFailure('Execution failed')


class PipesManager(FlagManager):

    def __init__(self, project):
        self.project = project
        self.pipes_namespace = PROCESS_NAMESPACE
        self.pipes_folder_files = [
            os.path.join(self.project_path, filename)
            for filename in os.listdir(self.project_path)
        ]
        self.__flag_prefix = self.project_path
        self.__pipelines = None

    @property
    def scheduler(self):
        return self.project.scheduler

    @property
    def config(self):
        return self.project.config

    @property
    def project_path(self):
        return self.project.path

    def _get_flag_prefix(self):
        return self.__flag_prefix

    @property
    def pipe_extension(self):
        return self.project.config['brainy']['pipe_extension']

    def get_class(self, pipe_type):
        pipe_type = self.pipes_namespace + '.' + pipe_type
        module_name, class_name = pipe_type.rsplit('.', 1)
        module = __import__(module_name, {}, {}, [class_name])
        return getattr(module, class_name)

    @property
    def pipelines(self):
        if self.__pipelines is None:
            # Repopulate dictionary.
            logger.info('Discovering pipelines.')
            pipes = dict()
            for definition_filename in self.pipes_folder_files:
                if not definition_filename.endswith(self.pipe_extension):
                    continue
                pipe = BrainyPipe(self)
                pipe.parse_definition_file(definition_filename)
                cls = self.get_class(pipe.definition['type'])
                # Note that we pass itself as a pipes_manager
                pipes[pipe.definition['name']] = cls(self, pipe.definition)
            self.__pipelines = self.sort_pipelines(pipes)
        return self.__pipelines

    def sort_pipelines(self, pipes):
        '''Reorder, tolerating declared dependencies found in definitions'''
        after_dag = dict()
        before_dag = dict()
        for depended_pipename in pipes:
            pipe = pipes[depended_pipename]
            if 'after' in pipe.definition:
                dependends_on = pipe.definition['after']
                if dependends_on not in after_dag:
                    after_dag[dependends_on] = list()
                after_dag[dependends_on].append(depended_pipename)
            if 'before' in pipe.definition:
                dependends_on = pipe.definition['before']
                if dependends_on not in before_dag:
                    before_dag[dependends_on] = list()
                before_dag[dependends_on].append(depended_pipename)

        def resolve_dependecy(name_a, name_b):
            # After
            if name_a in after_dag:
                if name_b not in after_dag:
                    # Second argument has no "after" dependencies.
                    if name_b in after_dag[name_a]:
                        return -1
                else:
                    # Second argument has "after" dependencies.
                    if name_b in after_dag[name_a] \
                            and name_a in after_dag[name_b]:
                        raise Exception('Recursive dependencies')
                    if name_b in after_dag[name_a]:
                        return -1
            if name_b in after_dag:
                if name_a not in after_dag:
                    # First argument has no "after" dependencies.
                    if name_a in after_dag[name_b]:
                        return 1
                else:
                    # First argument has "after" dependencies.
                    if name_a in after_dag[name_b] \
                            and name_b in after_dag[name_a]:
                        raise Exception('Recursive dependencies')
                    if name_a in after_dag[name_b]:
                        return 1
            # Before
            if name_a in before_dag:
                if name_b not in before_dag:
                    # Second argument has no "before" dependencies.
                    if name_b in before_dag[name_a]:
                        return 1
                else:
                    # Second argument has "before" dependencies.
                    if name_b in before_dag[name_a] \
                            and name_a in before_dag[name_b]:
                        raise Exception('Recursive dependencies')
                    if name_b in before_dag[name_a]:
                        return 1
            if name_b in before_dag:
                if name_a not in before_dag:
                    # First argument has no "before" dependencies.
                    if name_a in before_dag[name_b]:
                        return -1
                else:
                    # First argument has "before" dependencies.
                    if name_a in before_dag[name_b] \
                            and name_b in before_dag[name_a]:
                        raise Exception('Recursive dependencies')
                    if name_a in before_dag[name_b]:
                        return -1
            return 0

        pipenames = list(pipes.keys())
        sorted_pipenames = sorted(pipenames, cmp=resolve_dependecy)
        result = list()
        for pipename in sorted_pipenames:
            result.append(pipes[pipename])
        return result

    def execute_pipeline(self, pipeline):
        '''
        Execute passed pipeline process within the context of this
        PipesModule.
        '''
        try:
            BrainyReporter.append_report_pipe(pipeline.name)
            pipeline.communicate({'input': '{}'})
        except BrainyPipeFailure:
            # Errors are reported inside individual pipeline.
            logger.error('A pipeline has failed. We can not continue.')
            pipeline.has_failed = True

    def process_pipelines(self):
        BrainyReporter.start_report()
        previous_pipeline = None
        for pipeline in self.pipelines:
            # Check if current pipeline is dependent on previous one.
            depends_on_previous = False
            if previous_pipeline is not None:
                if 'before' in previous_pipeline.definition:
                    depends_on_previous = \
                        previous_pipeline.definition['before'] == pipeline.name
                elif 'after' in pipeline.definition:
                    depends_on_previous = \
                        previous_pipeline.name == pipeline.definition['after']

            if depends_on_previous and previous_pipeline.has_failed:
                logger.warn(('%s is skipped. Previous pipe that we depend on'
                            ' has failed or did not complete.') %
                            pipeline.name)
                # If previous pipeline we are dependent on has failed, then
                # mark pipeline as failed too to inform the next dependent
                # pipeline about the failure.
                pipeline.has_failed = True
                previous_pipeline = pipeline
                continue

            # Execute current pipeline.
            self.execute_pipeline(pipeline)

            # Remember as previous.
            previous_pipeline = pipeline
        BrainyReporter.finalize_report()
        BrainyReporter.save_report(self.project.report_prefix_path)
        BrainyReporter.update_or_generate_static_html(
            self.project.report_folder_path)

    def clean_pipelines_output(self):
        for pipeline in self.pipelines:
            if os.path.exists(pipeline.output_path):
                logger.warn('Recursively clean/remove subfolder: %s' %
                            pipeline.output_path)
                shutil.rmtree(pipeline.output_path)

    def run(self, command):
        if not hasattr(self, command):
            logger.error('Pipes manager does not know command called: %s' %
                         command)
            return
        try:
            # Obtain method.
            method = getattr(self, command)
            # Time method execution.
            timer = Timer()
            with timer:
                method()
            logger.info('Finished running <%s>. It took about %d (s)' % (
                        command, timer.duration_in_seconds()))
            report_data['duration_in_seconds'] = timer.duration_in_seconds()
        except Exception as error:
            # logger.error(error)
            logger.exception(error)
