from buildbot.process.buildstep import LoggingBuildStep, SUCCESS, FAILURE, EXCEPTION, SKIPPED
from twisted.internet import defer
from buildbot.steps.shell import ShellCommand
import re
from buildbot.util import epoch2datetime
from buildbot.util import safeTranslate
from buildbot.process.slavebuilder import IDLE, BUILDING
import random
def FormatDatetime(value):
    return value.strftime("%d_%m_%Y_%H_%M_%S_%z")

def mkdt(epoch):
    if epoch:
        return epoch2datetime(epoch)

@defer.inlineCallbacks
def updateSourceStamps(master, build, build_sourcestamps):
    # every build will generate at least one sourcestamp
    sourcestamps = build.build_status.getSourceStamps()

    build_sourcestampsetid = sourcestamps[0].sourcestampsetid

    sourcestamps_updated = build.build_status.getAllGotRevisions()
    build.build_status.updateSourceStamps()

    if len(sourcestamps_updated) > 0:
        update_ss = []
        for key, value in sourcestamps_updated.iteritems():
            update_ss.append(
                {'b_codebase': key, 'b_revision': value, 'b_sourcestampsetid': build_sourcestampsetid})

        rowsupdated = yield master.db.sourcestamps.updateSourceStamps(update_ss)

    # when running rebuild or passing revision as parameter
    for ss in sourcestamps:
        build_sourcestamps.append(
            {'b_codebase': ss.codebase, 'b_revision': ss.revision, 'b_branch': ss.branch,'b_sourcestampsetid': ss.sourcestampsetid})

class FindPreviousSuccessfulBuild(LoggingBuildStep):
    name = "Find Previous Successful Build"
    description="Searching for a previous successful build at the appropriate revision(s)..."
    descriptionDone="Searching complete."

    def __init__(self, **kwargs):
        self.build_sourcestamps = []
        self.master = None
        LoggingBuildStep.__init__(self, **kwargs)

    @defer.inlineCallbacks
    def start(self):
        if self.master is None:
            self.master = self.build.builder.botmaster.parent

        yield updateSourceStamps(self.master, self.build, self.build_sourcestamps)

        force_rebuild = self.build.getProperty("force_rebuild", False)
        if type(force_rebuild) != bool:
            force_rebuild = (force_rebuild.lower() == "true")

        force_chain_rebuild = self.build.getProperty("force_chain_rebuild", False)
        if type(force_chain_rebuild) != bool:
            force_chain_rebuild = (force_chain_rebuild.lower() == "true")

        if force_rebuild or force_chain_rebuild:
            self.step_status.setText(["Skipping previous build check (forcing a rebuild)."])
            # update merged buildrequest to reuse artifact generated by current buildrequest
            if len(self.build.requests) > 1:
                yield self.master.db.buildrequests.updateMergedBuildRequest(self.build.requests)
            self.finished(SKIPPED)
            return

        prevBuildRequest = yield self.master.db.buildrequests\
            .getBuildRequestBySourcestamps(buildername=self.build.builder.config.name,
                                           sourcestamps=self.build_sourcestamps)

        if prevBuildRequest:
            build_list = yield self.master.db.builds.getBuildsForRequest(prevBuildRequest['brid'])
            # there can be many builds per buildrequest for example (retry) when slave lost connection
            # in this case we will display all the builds related to this build request
            for build in build_list:
                build_num = build['number']
                friendly_name = self.build.builder.builder_status.getFriendlyName()
                url = yield self.master.status.getURLForBuildRequest(prevBuildRequest['brid'],
                                                                     self.build.builder.config.name, build_num, friendly_name)
                self.addURL(url['text'], url['path'])
            # we are not building but reusing a previous build
            reuse = yield self.master.db.buildrequests.reusePreviousBuild(self.build.requests, prevBuildRequest['brid'])
            self.step_status.setText(["Found previous successful build."])
            self.step_status.stepFinished(SUCCESS)
            self.build.result = SUCCESS
            self.build.setProperty("reusedOldBuild", True)
            self.build.allStepsDone()
        else:
            if len(self.build.requests) > 1:
                yield self.master.db.buildrequests.updateMergedBuildRequest(self.build.requests)
            self.step_status.setText(["Running build (previous sucessful build not found)."])

        self.finished(SUCCESS)
        return


class CheckArtifactExists(ShellCommand):
    name = "Check if Artifact Exists"
    description="Checking if artifacts exist from a previous build at the appropriate revision(s)..."
    descriptionDone="Searching complete."

    def __init__(self, artifact=None, artifactDirectory=None, artifactServer=None, artifactServerDir=None, artifactServerURL=None, stopBuild=True,**kwargs):
        self.master = None
        self.build_sourcestamps = []
        if not isinstance(artifact, list):
            artifact = [artifact]
        self.artifact = artifact
        self.artifactDirectory = artifactDirectory
        self.artifactServer = artifactServer
        self.artifactServerDir = artifactServerDir
        self.artifactServerURL = artifactServerURL
        self.artifactBuildrequest = None
        self.artifactPath = None
        self.artifactURL = None
        self.stopBuild = stopBuild
        ShellCommand.__init__(self, **kwargs)

    @defer.inlineCallbacks
    def createSummary(self, log):
        artifactlist = list(self.artifact)
        stdio = self.getLog('stdio').readlines()
        notfoundregex = re.compile(r'Not found!!')
        for l in stdio:
            m = notfoundregex.search(l)
            if m:
                break
            if len(artifactlist) == 0:
                break
            for a in artifactlist:
                artifact = a
                if artifact.endswith("/"):
                    artifact = artifact[:-1]
                foundregex = re.compile(r'(%s)' % artifact)
                m = foundregex.search(l)
                if (m):
                    artifactURL = self.artifactServerURL + "/" + self.artifactPath + "/" + a
                    self.addURL(a, artifactURL)
                    artifactlist.remove(a)

        if len(artifactlist) == 0:
            artifactsfound = self.build.getProperty("artifactsfound", True)

            if not artifactsfound:
                return
            else:
                self.build.setProperty("artifactsfound", True, "CheckArtifactExists %s" % self.artifact)
                self.build.setProperty("reusedOldBuild", True)

            if self.stopBuild:
                # update buildrequest (artifactbrid) with self.artifactBuildrequest
                reuse = yield self.master.db.buildrequests.reusePreviousBuild(self.build.requests, self.artifactBuildrequest['brid'])
                self.step_status.stepFinished(SUCCESS)
                self.build.result = SUCCESS
                self.build.allStepsDone()
        else:
            self.build.setProperty("artifactsfound", False, "CheckArtifactExists %s" % self.artifact)
            self.descriptionDone = ["Artifact not found on server %s." % self.artifactServerURL]
            # update merged buildrequest to reuse artifact generated by current buildrequest
            if len(self.build.requests) > 1:
                yield self.master.db.buildrequests.updateMergedBuildRequest(self.build.requests)

    @defer.inlineCallbacks
    def start(self):
        if self.master is None:
            self.master = self.build.builder.botmaster.parent

        yield updateSourceStamps(self.master, self.build, self.build_sourcestamps)

        force_rebuild = self.build.getProperty("force_rebuild", False)
        if type(force_rebuild) != bool:
            force_rebuild = (force_rebuild.lower() == "true")

        if force_rebuild:
            self.step_status.setText(["Skipping artifact check (forcing a rebuild)."])
            # update merged buildrequest to reuse artifact generated by current buildrequest
            if len(self.build.requests) > 1:
                yield self.master.db.buildrequests.updateMergedBuildRequest(self.build.requests)
            self.finished(SKIPPED)
            return

        self.artifactBuildrequest = yield self.master.db.buildrequests.getBuildRequestBySourcestamps(buildername=self.build.builder.config.name, sourcestamps=self.build_sourcestamps)

        if self.artifactBuildrequest:
            self.step_status.setText(["Artifact has been already generated."])
            self.artifactPath = "%s_%s_%s" % (self.build.builder.config.builddir,
                                              self.artifactBuildrequest['brid'], FormatDatetime(self.artifactBuildrequest['submitted_at']))

            if self.artifactDirectory:
                self.artifactPath += "/%s" %  self.artifactDirectory

            search_artifact = ""
            for a in self.artifact:
                if a.endswith("/"):
                    a = a[:-1]
                    if "/" in a:
                        index = a.rfind("/")
                        a = a[:index] + "/*"
                search_artifact += "; ls %s" % a

            command = ["ssh", self.artifactServer, "cd %s;" % self.artifactServerDir,
                       "if [ -d %s ]; then echo 'Exists'; else echo 'Not found!!'; fi;" % self.artifactPath,
                       "cd %s" % self.artifactPath, search_artifact, "; ls"]
            # ssh to the server to check if it artifact is there
            self.setCommand(command)
            ShellCommand.start(self)
            return

        if len(self.build.requests) > 1:
            yield self.master.db.buildrequests.updateMergedBuildRequest(self.build.requests)
        self.step_status.setText(["Artifact not found."])
        self.finished(SUCCESS)
        return


class CreateArtifactDirectory(ShellCommand):

    name = "Create Remote Artifact Directory"
    description="Creating the artifact directory on the remote artifacts server..."
    descriptionDone="Remote artifact directory created."

    def __init__(self,  artifactDirectory=None, artifactServer=None, artifactServerDir=None,  **kwargs):
        self.artifactDirectory = artifactDirectory
        self.artifactServer = artifactServer
        self.artifactServerDir = artifactServerDir
        ShellCommand.__init__(self, **kwargs)

    def start(self):
        br = self.build.requests[0]
        artifactPath  = "%s_%s_%s" % (self.build.builder.config.builddir,
                                      br.id, FormatDatetime(mkdt(br.submittedAt)))
        if (self.artifactDirectory):
            artifactPath += "/%s" % self.artifactDirectory


        command = ["ssh", self.artifactServer, "cd %s;" % self.artifactServerDir, "mkdir -p ",
                    artifactPath]

        self.setCommand(command)
        ShellCommand.start(self)

class UploadArtifact(ShellCommand):

    name = "Upload Artifact(s)"
    description="Uploading artifact(s) to remote artifact server..."
    descriptionDone="Artifact(s) uploaded."

    def __init__(self, artifact=None, artifactDirectory=None, artifactServer=None, artifactServerDir=None, artifactServerURL=None, **kwargs):
        self.artifact=artifact
        self.artifactURL = None
        self.artifactDirectory = artifactDirectory
        self.artifactServer = artifactServer
        self.artifactServerDir = artifactServerDir
        self.artifactServerURL = artifactServerURL
        ShellCommand.__init__(self, **kwargs)

    @defer.inlineCallbacks
    def start(self):
        br = self.build.requests[0]

        # this means that we are merging build requests with this one
        if len(self.build.requests) > 1:
            master = self.build.builder.botmaster.parent
            reuse = yield master.db.buildrequests.updateMergedBuildRequest(self.build.requests)

        artifactPath  = "%s_%s_%s" % (self.build.builder.config.builddir,
                                      br.id, FormatDatetime(mkdt(br.submittedAt)))
        if (self.artifactDirectory):
            artifactPath += "/%s" % self.artifactDirectory


        remotelocation = self.artifactServer + ":" + self.artifactServerDir + "/" + artifactPath + "/" + self.artifact.replace(" ", r"\ ")
        command = ["rsync", "-var", self.artifact, remotelocation]

        self.artifactURL = self.artifactServerURL + "/" + artifactPath + "/" + self.artifact
        self.addURL(self.artifact, self.artifactURL)
        self.setCommand(command)
        ShellCommand.start(self)

class DownloadArtifact(ShellCommand):
    name = "Download Artifact(s)"
    description="Downloading artifact(s) from the remote artifacts server..."
    descriptionDone="Artifact(s) downloaded."

    def __init__(self, artifactBuilderName=None, artifact=None, artifactDirectory=None, artifactDestination=None, artifactServer=None, artifactServerDir=None, **kwargs):
        self.artifactBuilderName = artifactBuilderName
        self.artifact = artifact
        self.artifactDirectory = artifactDirectory
        self.artifactServer = artifactServer
        self.artifactServerDir = artifactServerDir
        self.artifactDestination = artifactDestination or artifact
        self.master = None
        name = "Download Artifact for '%s'" % artifactBuilderName
        description = "Downloading artifact '%s'..." % artifactBuilderName
        descriptionDone="Downloaded '%s'." % artifactBuilderName
        ShellCommand.__init__(self, name=name, description=description, descriptionDone=descriptionDone,  **kwargs)

    @defer.inlineCallbacks
    def start(self):
        if self.master is None:
            self.master = self.build.builder.botmaster.parent

        #find artifact dependency
        triggeredbybrid = self.build.requests[0].id
        br = yield self.master.db.buildrequests.getBuildRequestTriggered(triggeredbybrid, self.artifactBuilderName)

        artifactPath  = "%s_%s_%s" % (safeTranslate(self.artifactBuilderName),
                                      br['brid'], FormatDatetime(br["submitted_at"]))
        if (self.artifactDirectory):
            artifactPath += "/%s" % self.artifactDirectory

        remotelocation = self.artifactServer + ":" +self.artifactServerDir + "/" + artifactPath + "/" + self.artifact
        command = ["rsync", "-var", remotelocation, self.artifactDestination]
        self.setCommand(command)
        ShellCommand.start(self)

from buildbot import locks

class AcquireBuildLocks(LoggingBuildStep):
    name = "Acquire Build Slave"
    description="Acquiring build slave..."
    descriptionDone="Build slave acquired."

    def __init__(self, hideStepIf = True, locks=None, **kwargs):
        self.initialLocks = locks
        self.locksAvailable = False
        LoggingBuildStep.__init__(self, hideStepIf = hideStepIf, locks=locks, **kwargs)


    def start(self):
        self.step_status.setText(["Acquiring build slave to complete build."])
        self.build.locks = self.locks

        if self.build.slavebuilder.state == IDLE:
            self.build.slavebuilder.state = BUILDING

        if self.build.builder.builder_status.currentBigState == "idle":
            self.build.builder.builder_status.setBigState("building")

        self.build.releaseLockInstanse = self
        self.finished(SUCCESS)
        return

    def releaseLocks(self):
        return

    def checkLocksAvailable(self, currentLocks):
        for lock, access in currentLocks:
            if not lock.isAvailable(self, access):
                return False
        return True

    def setupSlaveBuilder(self, ping_success, slavebuilder):
        # if not available builder we probably need to wait until we can get another builder
        if ping_success:
            self.build.setupSlaveBuilder(slavebuilder)

    def findAvailableSlaveBuilder(self):
        d = defer.succeed(None)
        slavebuilder = None
        if not self.locksAvailable and len(self.build.builder.getAvailableSlaveBuilders()) > 0:
            # setup a new slave for a builder prepare slavebuilder _startBuildFor process / builder.py
            slavebuilder = random.choice(self.build.builder.getAvailableSlaveBuilders())

        if slavebuilder is not None:
            d = slavebuilder.ping()
            d.addCallback(self.setupSlaveBuilder, slavebuilder)
        return d

    def startStep(self, remote):
        currentLocks =  self.setStepLocks(self.initialLocks)
        # TODO: there seems to be an issue when switching slaves 
        #self.locksAvailable = self.checkLocksAvailable(currentLocks)

        #d = self.findAvailableSlaveBuilder()
        #d.addCallback((lambda _: super(LoggingBuildStep, self).startStep(remote)))
        #return d
        return super(LoggingBuildStep, self).startStep(remote)


class ReleaseBuildLocks(LoggingBuildStep):
    name = "Release Builder Locks"
    description="Releasing builder locks..."
    descriptionDone="Build locks released."

    def __init__(self, hideStepIf = True, **kwargs):
        self.releaseLockInstanse
        LoggingBuildStep.__init__(self, hideStepIf=hideStepIf, **kwargs)

    def start(self):
        self.step_status.setText(["Releasing build locks."])
        self.locks = self.build.locks
        self.releaseLockInstanse = self.build.releaseLockInstanse
        # release slave lock
        self.build.slavebuilder.state = IDLE
        self.build.builder.builder_status.setBigState("idle")
        self.finished(SUCCESS)
        # notify that the slave may now be available to start a build.
        self.build.builder.botmaster.maybeStartBuildsForSlave(self.buildslave.slavename)
        return
