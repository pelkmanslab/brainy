'''
Testing brainy.process.code.* classes that help to submit custom user code.

Usage example:
    nosetests -vv -x --pdb test_customcode_processes
'''
import os
import shutil
import tempfile
from brainy_tests import BrainyTest, MockPipesManager
from brainy.pipes.Tools import relative_symlink
from testfixtures import LogCapture


MOCK_LINKING_FILEPATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'mock', 'linking',
)


def bake_an_empty_filepattern_list_pipe():
    return MockPipesManager('''
{
    # Define iBRAIN pipe type
    "type": "CellProfiler.Pipe",
    # Define chain of processes
    "chain": [
        {
            "type": "Tools.LinkFiles",
            "source_location": "''' + MOCK_LINKING_FILEPATH + '''",
            "target_location": "{data_path}",
            "file_patterns": {
                "hardlink": [],
                "symlink": []
            },
            "default_parameters": {
                "job_submission_queue": "8:00",
                "job_resubmission_queue": "36:00"
            }
        }
    ]
}
    \n''')


def bake_a_working_mock_pipe():
    return MockPipesManager('''
{
    # Define iBRAIN pipe type
    "type": "CellProfiler.Pipe",
    # Define chain of processes
    "chain": [
        {
            "type": "Tools.LinkFiles",
            "source_location": "{data_path}_old",
            "target_location": "{data_path}",
            "file_patterns": {
                "symlink": ["test_sym_linking*"],
                "hardlink": ["/^.*_hard_linking$/"]
            },
            "default_parameters": {
                "job_submission_queue": "8:00",
                "job_resubmission_queue": "36:00"
            }
        }
    ]
}
    \n''')


def bake_a_pipe_for_folder_linking():
    return MockPipesManager('''
{
    # Define iBRAIN pipe type
    "type": "CellProfiler.Pipe",
    # Define chain of processes
    "chain": [
        {
            "type": "Tools.LinkFiles",
            "source_location": "{project_path}",
            "target_location": "{project_path}",
            "file_patterns": {
                "symlink": ["DATA_*"]
            },
            "file_type": "d"
            #"recursively": 0
        }
    ]
}
    \n''')


class TestFileLinking(BrainyTest):

    def test_failing_pipe(self):
        '''Test LinkFiles: for failing if file pattern list is empty'''
        self.start_capturing()
        # Run pipes.
        pipes = bake_an_empty_filepattern_list_pipe()
        with LogCapture() as logs:
            pipes.process_pipelines()
        # Check output.
        self.stop_capturing()
        # print self.captured_output
        # print logs
        assert 'LinkFiles process requires a non empty list of file patterns '\
            'which can be match to files in source_location' in str(logs)

    def get_data_path(self, pipes):
        return self.get_first_process(pipes).data_path

    def fetch_expected_files(self, pipes):
        data_path = self.get_data_path(pipes)
        old_data_path = data_path + '_old'
        os.makedirs(old_data_path)
        expected_files = ['test_hard_linking', 'test_sym_linking',
                          'test_sym_linking2']
        for src_file in expected_files:
            shutil.copy(
                os.path.join(MOCK_LINKING_FILEPATH, src_file),
                old_data_path,
            )
        return (data_path, old_data_path, expected_files)

    def test_a_basic_linking(self):
        '''Test LinkFiles: for basic linking'''
        self.start_capturing()
        # Run pipes.
        pipes = bake_a_working_mock_pipe()
        # Do some mocking to make sure hard link is done on /tmp - same
        # file system.
        data_path, old_data_path, expected_files = \
            self.fetch_expected_files(pipes)
        #
        # FIRST RUN:
        #
        with LogCapture() as logs:
            pipes.process_pipelines()
        # Check output.
        self.stop_capturing()
        # print self.captured_output
        # print self.get_report_content(output=str(logs))
        # assert False
        assert 'Linking ' in self.get_report_content(output=str(logs))
        links = os.listdir(old_data_path)
        assert all([(expected_file in links)
                   for expected_file in expected_files])
        #
        # SECOND RUN:
        #
        # We want to test LinkFiles.has_data(). For this we just simulate
        # running pipes and attached pipeline with single tested
        # process type of interest, i.e. LinkFiles
        self.start_capturing()
        pipes.process_pipelines()
        self.stop_capturing()
        # has_data() should return True after second run
        process = self.get_first_process(pipes)
        assert process.has_data() is True
        assert process.is_resubmitted is False
        # Check if the pipeline we run twice has failed.
        for pipeline in pipes.pipelines:
            # E.g. this frequently breaks if has_data() fails
            assert pipeline.has_failed is False

    def test_folder_linking(self):
        '''Test LinkFiles: for folder linking'''
        self.start_capturing()
        # Run pipes.
        pipes = bake_a_pipe_for_folder_linking()
        # Do some mocking to make sure hard link is done on /tmp - same
        # file system.
        data_path, old_data_path, expected_files = \
            self.fetch_expected_files(pipes)
        raise
        sub_old = os.path.join(old_data_path, 'DATA_old')
        os.makedirs(sub_old)
        # Run the pipes.
        with LogCapture() as logs:
            pipes.process_pipelines()
        # Check output.
        self.stop_capturing()
        # print self.captured_output
        # print self.get_report_content()
        # assert False
        assert 'Linking ' in self.get_report_content(output=str(logs))
        result_link = os.path.join(pipes.env['plate_path'], 'DATA_old')
        assert os.path.exists(result_link) and os.path.islink(result_link)
        # assert False

    def test_relative_symlinking(self):
        parent = tempfile.mkdtemp()
        source = os.path.join(parent, 'some', 'upper', 'folder', 'aka',
                              'source')
        target = os.path.join(parent, 'some', 'target', 'folder', 'and',
                              'nested')
        os.makedirs(source)
        # os.makedirs(target)
        relative_symlink(source, target)
        # Check for value error.
        target = os.path.join(parent, 'some')
        try:
            relative_symlink(source, target)
        except ValueError as error:
            assert 'Source includes target pathname' in str(error)
        # Check for absolute path error.
        target = os.path.join('some', 'foo')
        try:
            relative_symlink(source, target)
        except ValueError as error:
            assert 'Arguments can be only absolute pathnames' in str(error)
        # Check for unshared prefix. ('requires sudo')
        # target = os.path.join('/','some','foo')
        # relative_symlink(source, target)

        # Reproducing a bug:
        foo_path = parent + '/mock_project/mock_test/data_of_linkfile'\
            + 's_old/test_hard_linking'
        bar_path = parent + '/mock_project/mock_test/data_of_linkfiles'\
            + '/test_hard_linking'
        os.makedirs(os.path.dirname(foo_path))
        relative_symlink(foo_path, bar_path)
        points_to = os.readlink(bar_path)
        assert points_to.startswith('../../')
