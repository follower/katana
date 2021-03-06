# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

from zope.interface import implements

from twisted.trial import unittest
from twisted.python import failure
from twisted.internet import defer, reactor
from buildbot import config, interfaces
from buildbot.process import properties
from buildbot.status import master
from buildbot.status.results import SUCCESS, FAILURE, EXCEPTION, DEPENDENCY_FAILURE
from buildbot.steps import trigger
from buildbot.test.util import steps, compat
from buildbot.test.fake import fakemaster, fakedb, fakebuild
from mock import Mock
from buildbot.process import factory
from buildbot.process.build import Build
from buildbot.process.builder import Builder

class FakeTriggerable(object):
    implements(interfaces.ITriggerableScheduler)

    triggered_with = None
    result = SUCCESS
    brids = {}
    exception = False

    def __init__(self, name):
        self.name = name

    def trigger(self, sourcestamps = None, set_props=None, triggeredbybrid=None, reason=None):
        self.triggered_with = (sourcestamps, set_props.properties, triggeredbybrid)
        d = defer.Deferred()
        if self.exception:
            reactor.callLater(0, d.errback, RuntimeError('oh noes'))
        else:
            reactor.callLater(0, d.callback, (self.result, self.brids))
        return d


class FakeSourceStamp(object):

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def asDict(self, includePatch = True):
        return self.__dict__.copy()

# Magic numbers that relate brid to other build settings
BRID_TO_BSID = lambda brid: brid+2000
BRID_TO_BID  = lambda brid: brid+3000
BRID_TO_BUILD_NUMBER = lambda brid: brid+4000

class TestTrigger(steps.BuildStepMixin, unittest.TestCase):

    def setUp(self):
        return self.setUpBuildStep()

    def tearDown(self):
        return self.tearDownBuildStep()

    def setupStep(self, step, sourcestampsInBuild=None, gotRevisionsInBuild=None, *args, **kwargs):
        sourcestamps = sourcestampsInBuild or []
        got_revisions = gotRevisionsInBuild or {}

        steps.BuildStepMixin.setupStep(self, step, *args, **kwargs)

        # This step reaches deeply into a number of parts of Buildbot.  That
        # should be fixed!

        # set up a buildmaster that knows about two fake schedulers, a and b
        m = fakemaster.make_master()
        self.build.builder.botmaster = m.botmaster
        m.db = fakedb.FakeDBConnector(self)
        m.status = master.Status(m)
        m.config.buildbotURL = "baseurl/"

        self.scheduler_a = a = FakeTriggerable(name='a')
        self.scheduler_b = b = FakeTriggerable(name='b')
        def allSchedulers():
            return [ a, b ]
        m.allSchedulers = allSchedulers

        a.brids = {'A': 11}
        b.brids = {'B': 22}

        make_fake_br = lambda brid, name: fakedb.BuildRequest(id=brid,
                                                              buildsetid=BRID_TO_BSID(brid),
                                                              buildername=name)
        make_fake_build = lambda brid: fakedb.Build(brid=brid,
                                                    id=BRID_TO_BID(brid),
                                                    number=BRID_TO_BUILD_NUMBER(brid))

        m.db.insertTestData([
               make_fake_br(11, "A"),
               make_fake_br(22, "B"),
               make_fake_build(11),
               make_fake_build(22),
        ])

        def getAllSourceStamps():
            return sourcestamps
        self.build.getAllSourceStamps = getAllSourceStamps
        def getAllGotRevisions():
            return got_revisions
        self.build.build_status.getAllGotRevisions = getAllGotRevisions

        request = Mock()
        request.id = 1

        self.build.requests = [request]

        self.exp_add_sourcestamp = None
        self.exp_a_trigger = None
        self.exp_b_trigger = None
        self.exp_added_urls = []

    def runStep(self, expect_waitForFinish=False):
        d = steps.BuildStepMixin.runStep(self)

        if expect_waitForFinish:
            # the build doesn't finish until after a callLater, so this has the
            # effect of checking whether the deferred has been fired already;
            # it should not have been!
            early = []
            d.addCallback(early.append)
            self.assertEqual(early, [])

        def check(_):
            self.assertEqual(self.scheduler_a.triggered_with,
                             self.exp_a_trigger)
            self.assertEqual(self.scheduler_b.triggered_with,
                             self.exp_b_trigger)
            self.assertEqual(self.step_status.addURL.call_args_list,
                             self.exp_added_urls)

            if self.exp_add_sourcestamp:
                self.assertEqual(self.addSourceStamp_kwargs,
                                self.exp_add_sourcestamp)
        d.addCallback(check)

        # pause runStep's completion until after any other callLater's are done
        def wait(_):
            d = defer.Deferred()
            reactor.callLater(0, d.callback, None)
            return d
        d.addCallback(wait)

        return d

    def expectTriggeredWith(self, a=None, b=None):
        self.exp_a_trigger = a
        self.exp_b_trigger = b

    def expectAddedSourceStamp(self, **kwargs):
        self.exp_add_sourcestamp = kwargs

    def expectTriggeredLinks(self, *args):
        def get_args(sch, name):
            label = lambda name, num: "%s #%d" % (name, num)
            url = lambda name, num: "baseurl/builders/%s/builds/%d" % (name, num )

            num = BRID_TO_BUILD_NUMBER(sch.brids[name])

            #returns the *args and **kwargs that will be called on addURL...
            #   which is just addURL('label', 'url')
            return ( (label(name,num), url(name,num)) , {} )

        if 'a' in args:
            self.exp_added_urls.append(get_args(self.scheduler_a, 'A'))
        if 'b' in args:
            self.exp_added_urls.append(get_args(self.scheduler_b, 'B'))


    # tests

    def test_no_schedulerNames(self):
        self.assertRaises(config.ConfigErrors, lambda :
                trigger.Trigger())

    def test_sourceStamp_and_updateSourceStamp(self):
        self.assertRaises(config.ConfigErrors, lambda :
                trigger.Trigger(schedulerNames=['c'],
                    sourceStamp=dict(x=1), updateSourceStamp=True))

    def test_sourceStamps_and_updateSourceStamp(self):
        self.assertRaises(config.ConfigErrors, lambda :
                trigger.Trigger(schedulerNames=['c'],
                    sourceStamps=[dict(x=1), dict(x=2)],
                    updateSourceStamp=True))

    def test_updateSourceStamp_and_alwaysUseLatest(self):
        self.assertRaises(config.ConfigErrors, lambda :
                trigger.Trigger(schedulerNames=['c'],
                    updateSourceStamp=True, alwaysUseLatest=True))

    def test_sourceStamp_and_alwaysUseLatest(self):
        self.assertRaises(config.ConfigErrors, lambda :
                trigger.Trigger(schedulerNames=['c'],
                    sourceStamp=dict(x=1), alwaysUseLatest=True))

    def test_sourceStamps_and_alwaysUseLatest(self):
        self.assertRaises(config.ConfigErrors, lambda :
                trigger.Trigger(schedulerNames=['c'],
                    sourceStamps=[dict(x=1), dict(x=2)],
                    alwaysUseLatest=True))

    def test_simple(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'], sourceStamps = {}))
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({}, {}, 1))
        return self.runStep()

    def test_simple_failure(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a']))
        self.scheduler_a.result = FAILURE
        # not waitForFinish, so trigger step succeeds even though the build
        # didn't fail
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({}, {}, 1))
        return self.runStep()

    @compat.usesFlushLoggedErrors
    def test_simple_exception(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a']))
        self.scheduler_a.exception = True
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({}, {}, 1))
        d = self.runStep()
        def flush(_):
            self.assertEqual(len(self.flushLoggedErrors(RuntimeError)), 1)
        d.addCallback(flush)
        return d

    def test_bogus_scheduler(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a', 'x']))
        self.expectOutcome(result=FAILURE, status_text=['not valid scheduler:', 'x'])
        self.expectTriggeredWith(a=None) # a is not triggered!
        return self.runStep()

    def test_updateSourceStamp(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                                       updateSourceStamp=True),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                              ],
                       gotRevisionsInBuild = {'': 23456},
                       )
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({'':{'codebase':'',
                                         'repository': 'x',
                                         'revision': 23456}
                                    }, {}, 1))
        return self.runStep()

    def test_updateSourceStamp_no_got_revision(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                                       updateSourceStamp=True),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                              ])
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({'':{'codebase':'',
                                         'repository': 'x',
                                         'revision': 11111} # uses old revision
                                    }, {}, 1))
        return self.runStep()

    def test_not_updateSourceStamp(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                                       updateSourceStamp=False),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                              ],
                       gotRevisionsInBuild = {'': 23456},
                       )
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({'':{'codebase':'',
                                          'repository': 'x',
                                          'revision': 11111}
                                    }, {}, 1))
        return self.runStep()

    def test_updateSourceStamp_multiple_repositories(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                                       updateSourceStamp=True),
                       sourcestampsInBuild = [
                                              FakeSourceStamp(codebase='cb1',
                                                              revision='12345'),
                                              FakeSourceStamp(codebase='cb2',
                                                              revision='12345')
                                              ],
                       gotRevisionsInBuild = {'cb1': 23456, 'cb2': 34567},
                       )
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({'cb1': {'codebase':'cb1',
                                             'revision':23456},
                                     'cb2': {'codebase':'cb2',
                                             'revision':34567}
                                    }, {}, 1))
        return self.runStep()

    def test_updateSourceStamp_prop_false(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                               updateSourceStamp=properties.Property('usess')),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                             ],
                       gotRevisionsInBuild = {'': 23456},
                       )
        self.properties.setProperty('usess', False, 'me')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        # didn't use got_revision
        self.expectTriggeredWith(a=({'': { 'codebase':'',
                                           'repository': 'x',
                                           'revision': 11111
                                    }}, {}, 1))
        return self.runStep()

    def test_updateSourceStamp_prop_true(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                               updateSourceStamp=properties.Property('usess')),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                             ],
                       gotRevisionsInBuild = {'': 23456},
                       )
        self.properties.setProperty('usess', True, 'me')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        # didn't use got_revision
        self.expectTriggeredWith(a=({'': { 'codebase':'',
                                           'repository': 'x',
                                           'revision': 23456
                                    }}, {}, 1))
        return self.runStep()

    def test_alwaysUseLatest(self):
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
                                       alwaysUseLatest=True),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                             ])
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        # Do not pass setid
        self.expectTriggeredWith(b=({}, {}, 1))
        return self.runStep()

    def test_alwaysUseLatest_prop_false(self):
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
                                       alwaysUseLatest=properties.Property('aul')),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                              ])
        self.properties.setProperty('aul', False, 'me')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        # didn't use latest
        self.expectTriggeredWith(b=({'': { 'codebase':'',
                                           'repository': 'x',
                                           'revision': 11111}
                                    }, {}, 1))
        return self.runStep()

    def test_alwaysUseLatest_prop_true(self):
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
                                       alwaysUseLatest=properties.Property('aul')),
                       sourcestampsInBuild = [FakeSourceStamp(codebase='',
                                                              repository='x',
                                                              revision=11111)
                                              ])
        self.properties.setProperty('aul', True, 'me')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        # didn't use latest
        self.expectTriggeredWith(b=({}, {}, 1))
        return self.runStep()

    def test_sourceStamp(self):
        ss = dict(revision=9876, branch='dev')
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
            sourceStamp=ss))
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        self.expectTriggeredWith(b=({'': ss}, {}, 1))
        return self.runStep()

    def test_set_of_sourceStamps(self):
        ss1 = dict(codebase='cb1', repository='r1', revision=9876, branch='dev')
        ss2 = dict(codebase='cb2',repository='r2', revision=5432, branch='dev')
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
            sourceStamps=[ss1,ss2]))
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        self.expectTriggeredWith(b=({'cb1':ss1, 'cb2':ss2}, {}, 1))
        return self.runStep()

    def test_set_of_sourceStamps_override_build(self):
        ss1 = dict(codebase='cb1', repository='r1', revision=9876, branch='dev')
        ss2 = dict(codebase='cb2',repository='r2', revision=5432, branch='dev')
        ss3 = FakeSourceStamp(codebase='cb3', repository='r3', revision=1234, branch='dev')
        ss4 = FakeSourceStamp(codebase='cb4',repository='r4', revision=2345, branch='dev')
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
            sourceStamps=[ss1,ss2]), sourcestampsInBuild=[ss3, ss4])
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        self.expectTriggeredWith(b=({'cb1':ss1, 'cb2':ss2}, {}, 1))
        return self.runStep()


    def test_sourceStamp_prop(self):
        self.setupStep(trigger.Trigger(schedulerNames=['b'],
            sourceStamp=dict(revision=properties.Property('rev'),
                branch='dev')))
        self.properties.setProperty('rev', 602, 'me')
        expected_ss = dict(revision=602, branch='dev')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'b'"])
        self.expectTriggeredWith(b=({'': expected_ss}, {}, 1))
        return self.runStep()

    def test_waitForFinish(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a', 'b'],
            waitForFinish=True))
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'", "'b'"])
        self.expectTriggeredWith(
            a=({}, {}, 1),
            b=({}, {}, 1))
        self.expectTriggeredLinks('a','b')
        return self.runStep(expect_waitForFinish=True)

    def test_waitForFinish_failure(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
            waitForFinish=True))
        self.scheduler_a.result = FAILURE
        self.expectOutcome(result=DEPENDENCY_FAILURE, status_text=['Dependency failed to build.'])
        self.expectTriggeredWith(a=({}, {}, 1))
        self.expectTriggeredLinks('a')
        return self.runStep(expect_waitForFinish=True)

    @compat.usesFlushLoggedErrors
    def test_waitForFinish_exception(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a', 'b'],
            waitForFinish=True))
        self.scheduler_b.exception = True
        self.expectOutcome(result=DEPENDENCY_FAILURE,
                        status_text=['Dependency failed to build.'])
        self.expectTriggeredWith(
            a=({}, {}, 1),
            b=({}, {}, 1))
        self.expectTriggeredLinks('a') # b doesnt return a brid
        d = self.runStep(expect_waitForFinish=True)
        def flush(_):
            self.assertEqual(len(self.flushLoggedErrors(RuntimeError)), 1)
        d.addCallback(flush)
        return d

    def test_set_properties(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                set_properties=dict(x=1, y=2)))
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({},
                                dict(x=(1, 'Trigger'), y=(2, 'Trigger')), 1))
        return self.runStep()

    def test_set_properties_prop(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                set_properties=dict(x=properties.Property('X'), y=2)))
        self.properties.setProperty('X', 'xxx', 'here')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({},
                                dict(x=('xxx', 'Trigger'), y=(2, 'Trigger')), 1))
        return self.runStep()

    def test_copy_properties(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
                copy_properties=['a', 'b']))
        self.properties.setProperty('a', 'A', 'AA')
        self.properties.setProperty('b', 'B', 'BB')
        self.properties.setProperty('c', 'C', 'CC')
        self.expectOutcome(result=SUCCESS, status_text=['Triggered:', "'a'"])
        self.expectTriggeredWith(a=({},
                            dict(a=('A', 'Trigger'),
                                 b=('B', 'Trigger')), 1))
        return self.runStep()

    def test_interrupt(self):
        self.setupStep(trigger.Trigger(schedulerNames=['a'],
            waitForFinish=True))
        self.expectOutcome(result=EXCEPTION, status_text=['Trigger', '(build was interrupted)'])
        self.expectTriggeredWith(a=({}, {}, 1))
        d = self.runStep(expect_waitForFinish=True)

        # interrupt before the callLater representing the Triggerable
        # schedulers completes
        self.step.interrupt(failure.Failure(RuntimeError('oh noes')))

        return d

    @defer.inlineCallbacks
    def test_interruptConnectionLostShouldRetry(self):
        self.master = fakemaster.FakeMaster()
        self.master.maybeStartBuildsForSlave = lambda slave: True

        self.master.db = fakedb.FakeDBConnector(self)

        rows = [fakedb.SourceStampSet(id=1),
                fakedb.SourceStamp(id=1, sourcestampsetid=1, codebase='c', branch="az", repository="xz", revision="ww"),
                fakedb.Buildset(id=1, reason='because', sourcestampsetid=1),
                fakedb.BuildRequest(id=1, buildsetid=1, buildername="bldr", submitted_at=130000)]

        yield self.master.db.insertTestData(rows)

        self.master.status = master.Status(self.master)
        self.master.config.buildbotURL = "baseurl/"

        self.scheduler_a = a = FakeTriggerable(name='a')
        self.scheduler_b = b = FakeTriggerable(name='b')
        def allSchedulers():
            return [a, b]
        self.master.allSchedulers = allSchedulers

        self.factory = factory.BuildFactory()
        self.step = trigger.Trigger(schedulerNames=['a'], waitForFinish=True)
        self.step.addCompleteLog = lambda x,y: True


        self.factory.addStep(self.step)
        config_args = dict(name="bldr", slavename="slv", builddir="bdir",
                     slavebuilddir="sbdir", project='default', factory=self.factory)
        builder_config = config.BuilderConfig(**config_args)

        self.bldr = Builder(builder_config.name, _addServices=False)
        self.bldr.master = self.master
        self.bldr.botmaster = self.master.botmaster

        mastercfg = config.MasterConfig()
        mastercfg.builders = [ builder_config ]

        self.bldr.startService()
        yield self.bldr.reconfigService(mastercfg)

        def newBuild(buildrequests):
            self.build = Build(buildrequests)
            self.build.setStepFactories([fakebuild.FakeStepFactory(self.step)])
            return self.build

        self.bldr.config.factory.newBuild = newBuild

        self.bldr.notifyRequestsRemoved = lambda x: True

        sb = Mock(spec=['isAvailable'], name='test-slave-1')
        sb.name = 'test-slave-1'
        sb.isAvailable.return_value = 1
        sb.slave = Mock()
        sb.slave.properties = properties.Properties()
        sb.prepare = lambda x, y: True
        sb.ping = lambda: True
        sb.buildStarted = lambda: True
        sb.buildFinished = lambda: False
        sb.remote = Mock()
        self.bldr.slaves.append(sb)

        self.assertEqual(self.master.db.buildrequests.claims, {})

        yield self.bldr.maybeStartBuild()

        self.assertEqual(self.master.db.buildrequests.claims[1].brid, 1)
        self.build.build_status.saveYourself = lambda: True
        self.build.currentStep.start()
        self.build.lostRemote()
        self.assertEqual(self.master.db.buildrequests.claims, {})

    @defer.inlineCallbacks
    def test_TriggerStepMultiMasterMode(self):
        m = fakemaster.FakeMaster()
        m.maybeStartBuildsForSlave = lambda slave: True
        m.status = master.Status(m)
        m.config.buildbotURL = "baseurl/"
        m.config.multiMaster = True
        m.db = fakedb.FakeDBConnector(self)

        scheduler_a = FakeTriggerable(name='a')

        m.allSchedulers = lambda: [scheduler_a]

        def trigger_sch(sourcestamps = None, set_props=None, triggeredbybrid=None, reason=None):
            rows = [ fakedb.MasterConfig(id=1,  buildbotURL="build-master-01/", objectid=1),
                     fakedb.MasterConfig(id=2, buildbotURL="build-master-02/", objectid=2),
                     fakedb.SourceStampSet(id=1),
                     fakedb.SourceStamp(id=1, sourcestampsetid=1, codebase='c',
                                        branch="az", repository="xz", revision="ww"),
                     fakedb.Buildset(id=1, reason='because', sourcestampsetid=1),
                     fakedb.BuildRequest(id=1, buildsetid=1, buildername="builder1", submitted_at=130000),
                     fakedb.BuildRequest(id=2, buildsetid=1, buildername="builder2", submitted_at=130000),
                     fakedb.BuildRequestClaim(brid=1, objectid=2, claimed_at=130000),
                     fakedb.BuildRequestClaim(brid=2, objectid=1, claimed_at=130000),
                     fakedb.Build(id=1, number=1, brid=1),
                     fakedb.Build(id=2, number=1, brid=2)]

            d = m.db.insertTestData(rows)
            d.addCallback(lambda _: (SUCCESS,  {'builder1': 1L, 'builder2': 2L}))
            return d

        scheduler_a.trigger = trigger_sch
        self.step = trigger.Trigger(schedulerNames=['a'], waitForFinish=True)
        self.step.addCompleteLog = lambda x,y: True
        self.step.step_status = Mock()
        self.step.step_status.getLogs = lambda: []
        self.expected_urls = []
        self.step.step_status.addURL = lambda text, path, results=None: \
            self.expected_urls.append({'text': text, 'path': path})
        self.step.build = fakebuild.FakeBuild()
        self.step.build.builder.botmaster = m.botmaster

        self.step.build.getAllSourceStamps = lambda: []
        self.step.build.build_status.getAllGotRevisions = lambda: {}
        request = Mock()
        request.id = 1
        self.step.build.requests = [request]

        self.remote = Mock(name="SlaveBuilder(remote)")
        yield self.step.startStep(self.remote)

        self.assertEqual(self.expected_urls[0],
                        {'text': 'builder2 #1', 'path': 'build-master-01/builders/builder2/builds/1'})
        self.assertEqual(self.expected_urls[1],
                        {'text': 'builder1 #1', 'path': 'build-master-02/builders/builder1/builds/1'})
