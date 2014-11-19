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

"""
Support for buildsets in the database
"""

import sqlalchemy as sa
from twisted.internet import reactor
from buildbot.util import json
from buildbot.db import base
from buildbot.util import epoch2datetime, datetime2epoch

class BsDict(dict):
    pass

class BuildsetsConnectorComponent(base.DBConnectorComponent):
    # Documentation is in developer/database.rst

    def addBuildset(self, sourcestampsetid, reason, properties, triggeredbybrid=None,
                    builderNames=None, external_idstring=None,  _reactor=reactor):
        def thd(conn):
            priority = 0
            buildsets_tbl = self.db.model.buildsets
            submitted_at = _reactor.seconds()

            reason_val = self.truncateColumn(buildsets_tbl.c.reason, reason)
            self.check_length(buildsets_tbl.c.reason, reason_val)
            self.check_length(buildsets_tbl.c.external_idstring,
                    external_idstring)

            transaction = conn.begin()

            # insert the buildset itself
            r = conn.execute(buildsets_tbl.insert(), dict(
                sourcestampsetid=sourcestampsetid, submitted_at=submitted_at,
                reason=reason_val, complete=0, complete_at=None, results=-1,
                external_idstring=external_idstring))
            bsid = r.inserted_primary_key[0]

            # add any properties
            if properties:
                bs_props_tbl = self.db.model.buildset_properties
                if 'priority' in properties:
                    priority = properties.get('priority')[0]

                inserts = [
                    dict(buildsetid=bsid, property_name=k,
                         property_value=json.dumps([v,s]))
                    for k,(v,s) in properties.iteritems() ]
                for i in inserts:
                    self.check_length(bs_props_tbl.c.property_name,
                                      i['property_name'])
                    self.check_length(bs_props_tbl.c.property_value,
                                      i['property_value'])

                conn.execute(bs_props_tbl.insert(), inserts)

            # and finish with a build request for each builder.  Note that
            # sqlalchemy and the Python DBAPI do not provide a way to recover
            # inserted IDs from a multi-row insert, so this is done one row at
            # a time.
            brids = {}
            br_tbl = self.db.model.buildrequests
            startbrid = triggeredbybrid
            if triggeredbybrid is not None:
                q = sa.select([br_tbl.c.triggeredbybrid, br_tbl.c.startbrid]) \
                    .where(br_tbl.c.id == triggeredbybrid)

                res = conn.execute(q)
                row = res.fetchone()
                if row and (row.startbrid is not None):
                    startbrid = row.startbrid

            ins = br_tbl.insert()
            for buildername in builderNames:
                self.check_length(br_tbl.c.buildername, buildername)
                res = conn.execute(ins,
                    dict(buildsetid=bsid, buildername=buildername, priority=priority,
                        claimed_at=0, claimed_by_name=None,
                        claimed_by_incarnation=None, complete=0, results=-1,
                        submitted_at=submitted_at, complete_at=None,
                        triggeredbybrid=triggeredbybrid, startbrid=startbrid))

                brids[buildername] = res.inserted_primary_key[0]

            transaction.commit()

            return (bsid, brids)
        return self.db.pool.do(thd)

    def completeBuildset(self, bsid, results, complete_at=None,
                                _reactor=reactor):
        if complete_at is not None:
            complete_at = datetime2epoch(complete_at)
        else:
            complete_at = _reactor.seconds()

        def thd(conn):
            tbl = self.db.model.buildsets
            def update():
                q = tbl.update(whereclause=(
                    (tbl.c.id == bsid) &
                    ((tbl.c.complete_at == None) | (tbl.c.complete != 1))))
                res = conn.execute(q,
                    complete=1,
                    results=results,
                    complete_at=complete_at)

                return (res.rowcount > 0)

            # maybe another build completed the buildset
            def checkupdated():
                q = tbl.select(whereclause=((tbl.c.id == bsid)
                               & (tbl.c.complete==1) & (tbl.c.complete_at != None)))
                res = conn.execute(q)
                row = res.fetchone()
                res.close()
                if not row:
                    raise KeyError
                    
            if update():               
                return
            else:
                checkupdated()

        return self.db.pool.do(thd)

    def getBuildset(self, bsid):
        def thd(conn):
            bs_tbl = self.db.model.buildsets
            q = bs_tbl.select(whereclause=(bs_tbl.c.id == bsid))
            res = conn.execute(q)
            row = res.fetchone()
            if not row:
                return None
            return self._row2dict(row)
        return self.db.pool.do(thd)

    def getBuildsets(self, complete=None):
        def thd(conn):
            bs_tbl = self.db.model.buildsets
            q = bs_tbl.select()
            if complete is not None:
                if complete:
                    q = q.where(bs_tbl.c.complete != 0)
                else:
                    q = q.where((bs_tbl.c.complete == 0) |
                                (bs_tbl.c.complete == None))
            res = conn.execute(q)
            return [ self._row2dict(row) for row in res.fetchall() ]
        return self.db.pool.do(thd)

    def getBuildsetProperties(self, buildsetid):
        """
        Return the properties for a buildset, in the same format they were
        given to L{addBuildset}.

        Note that this method does not distinguish a nonexistent buildset from
        a buildset with no properties, and returns C{{}} in either case.

        @param buildsetid: buildset ID

        @returns: dictionary mapping property name to (value, source), via
        Deferred
        """
        def thd(conn):
            bsp_tbl = self.db.model.buildset_properties
            q = sa.select(
                [ bsp_tbl.c.property_name, bsp_tbl.c.property_value ],
                whereclause=(bsp_tbl.c.buildsetid == buildsetid))
            l = []
            for row in conn.execute(q):
                try:
                    properties = json.loads(row.property_value)
                    l.append((row.property_name,
                           tuple(properties)))
                except ValueError:
                    pass
            return dict(l)
        return self.db.pool.do(thd)

    def _row2dict(self, row):
        def mkdt(epoch):
            if epoch:
                return epoch2datetime(epoch)
        return BsDict(external_idstring=row.external_idstring,
                reason=row.reason, sourcestampsetid=row.sourcestampsetid,
                submitted_at=mkdt(row.submitted_at),
                complete=bool(row.complete),
                complete_at=mkdt(row.complete_at), results=row.results,
                bsid=row.id)
