"""
Test ability to freeze and run app with an update available.

May require admin privileges on Windows, due to issue where
PyInstaller doesn't embed a sensible default manifest in the EXE.
"""
from argparse import Namespace
import gzip
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

import appdirs
import bsdiff4
import ed25519
import psutil
import six

import wxupdatedemo

from dsdev_utils.system import get_system
from dsdev_utils.crypto import get_package_hashes
from pyupdater import settings
from pyupdater.builder import Builder

logger = logging.getLogger(__name__)

APP_NAME = 'PyUpdaterBuildTester'
COMPANY_NAME = 'Test Company'
CURRENT_VERSION = '1.2.3'
UPDATE_VERSION = '1.2.5'
# Pretend all file sizes are 10000 bytes for now:
FILE_SIZE = 10000
# Pretend all patch sizes are 250 bytes for now:
PATCH_SIZE = 250
# PyUpdater version format is:
# Major.Minor.Patch.[Alpha|Beta|Stable].ReleaseNumber
# where Alpha=0, Beta=1 and Stable=2
CURRENT_VERSION_PYU_FORMAT = '%s.2.0' % CURRENT_VERSION
UPDATE_VERSION_PYU_FORMAT = '%s.2.0' % UPDATE_VERSION

# pylint: disable=bad-continuation
VERSIONS = {
  'updates': {
    APP_NAME : {
      CURRENT_VERSION_PYU_FORMAT: {
        'mac': {
          'file_hash': None,
          'filename': '%s-mac-%s.tar.gz' % (APP_NAME, CURRENT_VERSION),
          'file_size': FILE_SIZE
        },
        'win': {
          'file_hash': None,
          'filename': '%s-win-%s.zip' % (APP_NAME, CURRENT_VERSION),
          'file_size': FILE_SIZE
        }
      },
      UPDATE_VERSION_PYU_FORMAT: {
        'mac': {
          'file_hash': None,
          'filename': '%s-mac-%s.tar.gz' % (APP_NAME, UPDATE_VERSION),
          'file_size': FILE_SIZE,
          'patch_name': '%s-mac-2' % APP_NAME,
          'patch_hash': None,
          'patch_size': PATCH_SIZE

        },
        'win': {
          'file_hash': None,
          'filename': '%s-win-%s.zip' % (APP_NAME, UPDATE_VERSION),
          'file_size': FILE_SIZE,
          'patch_name': '%s-win-2' % APP_NAME,
          'patch_hash': None,
          'patch_size': PATCH_SIZE
        }
      }
    }
  },
  'latest': {
    APP_NAME: {
      'stable': {
        'mac': UPDATE_VERSION_PYU_FORMAT,
        'win': UPDATE_VERSION_PYU_FORMAT
      }
    }
  }
}

# Generated by "pyupdater keys -c":
# These keys are only used for automated testing!
# DO NOT SHARE YOUR PRODUCTION PRIVATE_KEY !!!
PUBLIC_KEY = "12y2oHGB2oroRQJkR73CJNaFeQy776oXsUrqWaAEiZU"
PRIVATE_KEY = "nHgoNwSmXSDNSMqQTtdAEmi/6otajiNYJEXESvAO8dc"

KEYS = {
  "app_public": "MIBCEwFh7AcaxJrHKIgYqAmZ9YX16NXVHLi+EdDmtYc",
  "signature": ("1YTDuJauq7qVFUrKPHGMMESllJ4umo6u5r9pEgVmvlxgXi3qGXnKWo2LG94"
                "+oosN3KiO8DlxOmyfuwaaQKtFCw")
}


def PidIsRunning(pid):
    """
    Check if a process with PID pid is running.
    """
    try:
        proc = psutil.Process(int(pid))
        if proc.status == psutil.STATUS_DEAD:
            return False
        if proc.status == psutil.STATUS_ZOMBIE:
            return False
        return True  # Assume other status are valid
    except psutil.NoSuchProcess:
        return False


class FreezeUpdateAvailableTester(unittest.TestCase):
    """
    Test ability to freeze and run app with an update available.
    """
    def __init__(self, *args, **kwargs):
        super(FreezeUpdateAvailableTester, self).__init__(*args, **kwargs)
        self.initialWorkingDir = None
        self.fileServerDir = None
        # self.tempDir contains .pyupdater/config.pyu, pyu-data/new/ :
        self.tempDir = None
        self.updateFilename = None
        self.originalVersion = wxupdatedemo.__version__

    def setUp(self):
        # pylint: disable=too-many-statements
        # pylint: disable=too-many-locals
        self.initialWorkingDir = os.getcwd()

        userDataDir = appdirs.user_data_dir(APP_NAME, COMPANY_NAME)
        if os.path.exists(userDataDir):
            shutil.rmtree(userDataDir)
        os.makedirs(userDataDir)
        versionsUserDataFilePath = os.path.join(userDataDir, 'versions.gz')
        userDataUpdateDir = os.path.join(userDataDir, "update")
        os.mkdir(userDataUpdateDir)
        system = get_system()
        self.currentFilename = \
            VERSIONS['updates'][APP_NAME][CURRENT_VERSION_PYU_FORMAT]\
            [system]['filename']
        currentFilePath = os.path.join(userDataUpdateDir, self.currentFilename)
        with open(currentFilePath, "wb") as currentFile:
            currentFile.write("%s" % CURRENT_VERSION)
            currentFile.seek(FILE_SIZE - 1)
            currentFile.write("\0")
        fileHash = get_package_hashes(currentFilePath)
        VERSIONS['updates'][APP_NAME][CURRENT_VERSION_PYU_FORMAT]\
            [system]['file_hash'] = fileHash

        tempFile = tempfile.NamedTemporaryFile()
        self.fileServerDir = tempFile.name
        tempFile.close()
        os.mkdir(self.fileServerDir)
        os.chdir(self.fileServerDir)

        self.updateFilename = \
            VERSIONS['updates'][APP_NAME][UPDATE_VERSION_PYU_FORMAT]\
            [system]['filename']
        with open(self.updateFilename, "wb") as updateFile:
            updateFile.write("%s" % UPDATE_VERSION)
            updateFile.seek(FILE_SIZE - 1)
            updateFile.write("\0")
        os.chdir(self.fileServerDir)
        fileHash = get_package_hashes(self.updateFilename)
        VERSIONS['updates'][APP_NAME][UPDATE_VERSION_PYU_FORMAT]\
            [system]['file_hash'] = fileHash
        self.patchFilename = \
            VERSIONS['updates'][APP_NAME][UPDATE_VERSION_PYU_FORMAT]\
            [system]['patch_name']
        bsdiff4.file_diff(currentFilePath, self.updateFilename,
                          self.patchFilename)
        os.chdir(self.fileServerDir)
        fileHash = get_package_hashes(self.patchFilename)
        VERSIONS['updates'][APP_NAME][UPDATE_VERSION_PYU_FORMAT]\
            [system]['patch_hash'] = fileHash
        os.chdir(self.initialWorkingDir)
        privateKey = ed25519.SigningKey(PRIVATE_KEY.encode('utf-8'),
                                        encoding='base64')
        signature = privateKey.sign(six.b(json.dumps(VERSIONS, sort_keys=True)),
                                    encoding='base64').decode()
        VERSIONS['signature'] = signature
        keysFilePath = os.path.join(self.fileServerDir, 'keys.gz')
        with gzip.open(keysFilePath, 'wb') as keysFile:
            keysFile.write(json.dumps(KEYS, sort_keys=True))
        versionsFilePath = os.path.join(self.fileServerDir, 'versions.gz')
        with gzip.open(versionsFilePath, 'wb') as versionsFile:
            versionsFile.write(json.dumps(VERSIONS, sort_keys=True))
        with gzip.open(versionsUserDataFilePath, 'wb') as versionsFile:
            versionsFile.write(json.dumps(VERSIONS, sort_keys=True))

        tempFile = tempfile.NamedTemporaryFile()
        self.tempDir = tempFile.name
        tempFile.close()
        settings.CONFIG_DATA_FOLDER = os.path.join(self.tempDir, '.pyupdater')
        settings.USER_DATA_FOLDER = os.path.join(self.tempDir, 'pyu-data')
        os.mkdir(self.tempDir)
        os.mkdir(settings.USER_DATA_FOLDER)
        os.mkdir(settings.CONFIG_DATA_FOLDER)
        # The way we set the App name below avoids having to
        # create .pyupdater/config.pyu:
        settings.GENERIC_APP_NAME = APP_NAME
        settings.GENERIC_COMPANY_NAME = COMPANY_NAME
        os.environ['PYUPDATER_FILESERVER_DIR'] = self.fileServerDir
        os.environ['WXUPDATEDEMO_TESTING'] = 'True'
        os.environ['WXUPDATEDEMO_TESTING_FROZEN'] = 'True'
        os.environ['WXUPDATEDEMO_TESTING_APP_NAME'] = APP_NAME
        os.environ['WXUPDATEDEMO_TESTING_COMPANY_NAME'] = COMPANY_NAME
        os.environ['WXUPDATEDEMO_TESTING_APP_VERSION'] = CURRENT_VERSION
        os.environ['WXUPDATEDEMO_TESTING_PUBLIC_KEY'] = PUBLIC_KEY

    def test_freeze_update_available(self):
        """
        Test ability to freeze and run app with an update available.
        """
        # pylint: disable=too-many-branches
        # pylint: disable=too-many-locals
        # pylint: disable=too-many-statements

        # PyUpdater uses PyInstaller under the hood.  We will customize
        # the command-line arguments PyUpdater sends to PyInstaller.

        # The SocketServer module (used by werkzeug, which is used by Flask)
        # doesn't seem to get detected automatically by PyInstaller (observed
        # on Windows), so we add this as a hidden import.

        pyiArgs = ['--hidden-import=SocketServer', 'run.py']

        if get_system() == 'mac':
            # On Mac, we need to use PyInstaller's --windowed option to create
            # an app bundle, otherwise attempting to run the frozen application
            # gives this error:
            #
            #     This program needs access to the screen.
            #     Please run with a Framework build of python, and only when
            #     you are logged in on the main display of your Mac.
            #
            # On other platforms, we will build a console application for the
            # purposes of testing, so that we can easily interact with its
            # STDOUT and STDERR:
            pyiArgs = ['--windowed'] + pyiArgs
        else:
            pyiArgs = ['--console'] + pyiArgs
        wxupdatedemo.__version__ = CURRENT_VERSION
        args = Namespace(app_version=CURRENT_VERSION, clean=False,
                         command='build', distpath=None, keep=False,
                         name=None, onedir=False, onefile=False,
                         specpath=None, workpath=None)
        builder = Builder(args, pyiArgs)
        builder.build()
        if get_system() == 'win':
            ext = '.zip'
        else:
            ext = '.tar.gz'
        buildFilename = \
            '%s-%s-%s%s' % (APP_NAME, get_system(), CURRENT_VERSION, ext)
        newDir = os.path.join(settings.USER_DATA_FOLDER, 'new')
        self.assertEqual(os.listdir(newDir), [buildFilename])
        os.chdir(newDir)
        if get_system() == 'win':
            with zipfile.ZipFile(buildFilename, 'r') as zipFile:
                zipFile.extractall()
            pathToExe = '%s.exe' % APP_NAME
            self.assertEqual(sorted(os.listdir('.')),
                             [buildFilename, pathToExe])
        elif get_system() == 'mac':
            tar = tarfile.open(buildFilename, "r:gz")
            tar.extractall()
            tar.close()
            appBundleName = '%s.app' % APP_NAME
            self.assertEqual(sorted(os.listdir('.')),
                             [buildFilename, appBundleName])
            pathToExe = os.path.join(newDir, '%s.app' % APP_NAME,
                                     'Contents', 'MacOS', APP_NAME)
        else:  # Linux / Unix
            tar = tarfile.open(buildFilename, "r:gz")
            tar.extractall()
            tar.close()
            pathToExe = APP_NAME

        sys.stderr.write("\n\nTesting ability to apply patch update...\n")

        cmdList = [pathToExe, '--debug']
        runExeProc = subprocess.Popen(cmdList,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      env=os.environ.copy())
        runExeStdout, _ = runExeProc.communicate()
        logger.debug(runExeStdout)
        self.assertEqual(runExeProc.returncode, 0)
        appliedPatchSuccessfully = False
        statusPrefix = "Exiting with status: "
        for line in runExeStdout.splitlines():
            if "Applied patch successfully" in line:
                sys.stderr.write("\t%s\n" % line)
                appliedPatchSuccessfully = True
            if line.startswith("Exiting with status: "):
                sys.stderr.write("\t%s\n" % line)
                status = line.split(statusPrefix)[1]
                self.assertEqual(status, "Extracting update and restarting.")
        self.assertTrue(appliedPatchSuccessfully)

        # Remove all local data from previous PyUpdater downloads:
        if os.path.exists(appdirs.user_data_dir(APP_NAME, COMPANY_NAME)):
            shutil.rmtree(appdirs.user_data_dir(APP_NAME, COMPANY_NAME))
        # Now we can't patch because there's no base binary to patch from:
        sys.stderr.write("\nTesting ability to download full update...\n")
        runExeProc = subprocess.Popen(cmdList,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      env=os.environ.copy())
        runExeStdout, _ = runExeProc.communicate()
        logger.debug(runExeStdout)
        self.assertEqual(runExeProc.returncode, 0)
        fullDownloadSuccessful = False
        statusPrefix = "Exiting with status: "
        for line in runExeStdout.splitlines():
            if "Full download successful" in line:
                sys.stderr.write("\t%s\n" % line)
                fullDownloadSuccessful = True
            if line.startswith("Exiting with status: "):
                sys.stderr.write("\t%s\n" % line)
                status = line.split(statusPrefix)[1]
                self.assertEqual(status, "Extracting update and restarting.")
        self.assertTrue(fullDownloadSuccessful)

        # Remove all local data from previous PyUpdater downloads:
        if os.path.exists(appdirs.user_data_dir(APP_NAME, COMPANY_NAME)):
            shutil.rmtree(appdirs.user_data_dir(APP_NAME, COMPANY_NAME))
        # Remove update archive from file server:
        os.remove(os.path.join(self.fileServerDir,
                               self.updateFilename))
        # Now attempting to update should fail - can't download update.
        sys.stderr.write(
            "\nTesting ability to report failed download of update...\n")
        runExeProc = subprocess.Popen(cmdList,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      env=os.environ.copy())
        runExeStdout, _ = runExeProc.communicate()
        logger.debug(runExeStdout)
        self.assertEqual(runExeProc.returncode, 0)
        statusPrefix = "Exiting with status: "
        fullDownloadFailed = False
        for line in runExeStdout.splitlines():
            if "Full download failed" in line:
                sys.stderr.write("\t%s\n" % line)
                fullDownloadFailed = True
            if line.startswith("Exiting with status: "):
                sys.stderr.write("\t%s\n" % line)
                status = line.split(statusPrefix)[1]
                self.assertEqual(status, "Update download failed.")
        self.assertTrue(fullDownloadFailed)


    def tearDown(self):
        """
        Clean up.
        """
        wxupdatedemo.__version__ = self.originalVersion
        try:
            shutil.rmtree(self.tempDir)
        except OSError:
            logger.warning("Couldn't remove %s", self.tempDir)
        os.chdir(self.initialWorkingDir)
        try:
            shutil.rmtree(self.fileServerDir)
        except OSError:
            logger.warning("Couldn't remove %s", self.fileServerDir)
        del os.environ['PYUPDATER_FILESERVER_DIR']
        del os.environ['WXUPDATEDEMO_TESTING']
        del os.environ['WXUPDATEDEMO_TESTING_FROZEN']
        del os.environ['WXUPDATEDEMO_TESTING_APP_NAME']
        del os.environ['WXUPDATEDEMO_TESTING_COMPANY_NAME']
        del os.environ['WXUPDATEDEMO_TESTING_APP_VERSION']
        del os.environ['WXUPDATEDEMO_TESTING_PUBLIC_KEY']
        if os.path.exists(appdirs.user_data_dir(APP_NAME, COMPANY_NAME)):
            try:
                shutil.rmtree(appdirs.user_data_dir(APP_NAME, COMPANY_NAME))
            except OSError:
                logger.warning("Couldn't remove %s",
                               appdirs.user_data_dir(APP_NAME, COMPANY_NAME))
