import Poem.django_logging
import logging, urllib
import httplib

from django.utils import simplejson
from Poem.poem.models import Profile, MetricInstance
from urlparse import urlparse
from Poem import settings

LOG_INSTANCE = logging.getLogger('POEMIMPORTPROFILES')

class SyncException(Exception):
    def __init__(self, value):
        self.parameter = value

    def __str__(self):
        return repr(self.parameter)

class PoemSync(object):
    def __init__(self, url="", profile_list=None):
        self._base_url = url
        self._has_error = False
        self._raise_exception = True
        self._profile_exist_list = []
        self._profile_list = profile_list

    def _raise_error(self, error_str):
        self._has_error = True
        msg_str = "SyncException : %s" % (error_str)
        LOG_INSTANCE.error(msg_str)
        if self._raise_exception:
            raise SyncException(msg_str)
        else:
            return None

    def update_obj(self, obj, dict_ob):
        for key, val in dict_ob.items():
            if hasattr(obj, key):
                setattr(obj, key, val)

    def sync_metricinstances(self, list_object, pobj):
        pobj.metric_instances.all().delete()
        for mins_dict in list_object:
                MetricInstance.objects.create(profile=pobj, metric=mins_dict['metric'],
                    service_flavour=mins_dict['atp_service_type_flavour'],
                    vo=mins_dict['vo'], fqan=mins_dict['fqan'])


    def sync_profile(self, p_dict):
        """ Sync A profile Object and all its components """
        # clear the Error flag
        self._has_error = False
        try:
            key='vo'
            try: p_dict[key]
            except KeyError: key='atp_vo'
            pobj1 = Profile(name=p_dict['name'],
                    version=p_dict['version'],
                    vo=p_dict[key],
                    description=p_dict['description']
                    )
            try:
                pobj = Profile.objects.get(name=p_dict['name'], version=p_dict['version'])
                self._profile_exist_list.append(pobj)
            except Profile.DoesNotExist:
                pobj = None

            if self._has_error:
                raise SyncException('Unable to Sync')
        except ValueError, (ve):
            return self._raise_error("From sync_profile ValueError: (%s): %s" % (p_dict['name'], ve))
        except KeyError, (ke):
            return self._raise_error("From sync_profile KeyError: (%s): %s" % (p_dict['name'], ke))
        except SyncException, (ex):
            return self._raise_error('From sync_profile: (%s): %s' % (p_dict['name'], ex))

        try:
            pobj = Profile.objects.get(name=p_dict['name'], version=p_dict['version'])
            pobj.description = pobj1.description
            pobj.vo = pobj1.vo
        except Profile.DoesNotExist:
            pobj = pobj1

        try:
            pobj.save()
        except Exception, (ex):
            return self._raise_error('Saving Profile: %s %s' % (pobj, ex))

        self.sync_metricinstances(p_dict['metric_instances'], pobj)

        pobj.save()
        LOG_INSTANCE.info('Synchronized profile %s' % (pobj))
        return pobj

    def sync_object_list(self, object_list, function_name):
        ob_list = []
        res = self._raise_exception
        self._raise_exception = False
        for ob in object_list:
            # from poem instance import selectively
            if self._profile_list and ob['name'] in self._profile_list:
                obj = function_name(ob)
                ob_list.append(obj)
        self._raise_exception = res
        return ob_list

    def get_data_url(self, url, append):
        if not url:
            url = self._base_url
        try:
            o = urlparse(url)
            if o.scheme.startswith('file'):
                dcstr = simplejson.JSONDecoder().decode(open('/'+o.hostname+o.path).read())
            else:
                if o.scheme.startswith('https'):
                    conn = httplib.HTTPSConnection(host=o.netloc,
                                                   key_file=settings.HOST_KEY,
                                                   cert_file=settings.HOST_CERT)
                else:
                    conn = httplib.HTTPConnection(host=o.netloc)
                conn.putrequest('GET', o.path+'?'+o.query)
                conn.endheaders()
                dcstr = simplejson.JSONDecoder().decode(conn.getresponse().read())
        except IOError, (er):
            self._raise_error("Error Occurred while Retrieving URL: %s%s : %s" % (url, append, er))
        except Exception, (er):
            self._raise_error("Error Occurred while JSON decode: %s" % (er))
        return dcstr

    def sync_profile_list_from_url(self, url=None):
        return self.sync_object_list(self.get_data_url(url, ''), self.sync_profile)
