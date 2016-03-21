from django.forms import ModelForm, ModelMultipleChoiceField, CharField, Textarea
from django.forms.widgets import SelectMultiple, TextInput, Select
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.templatetags.admin_static import static
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth import forms
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from django.core import exceptions, validators
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.forms import ValidationError, models
from django.forms.util import ErrorList
from django.forms.util import flatatt
from django.http import HttpResponseRedirect, HttpResponse
from django.utils.encoding import force_unicode, force_text
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import ungettext, ugettext, ugettext_lazy as _
from itertools import chain

from Poem import poem
from django.contrib import auth

from Poem.poem.models import MetricInstance, Profile, UserProfile, VO, ServiceFlavour, Metrics
from Poem.poem import widgets
from Poem.poem.lookups import check_cache
from ajax_select import make_ajax_field

class SharedInfo:
    def __init__(self, requser=None):
        if requser:
            self.__class__.user = requser

    def getuser(self):
        if getattr(self.__class__, 'user', None):
            return self.__class__.user
        else:
            return None

class MyModelMultipleChoiceField(ModelMultipleChoiceField):
    def __init__(self, *args, **kwargs):
        self.ftype = kwargs.pop('ftype', None)
        super(ModelMultipleChoiceField, self).__init__(*args, **kwargs)

    def clean(self, value):
        if self.required and not value:
            raise ValidationError(self.error_messages['required'])
        elif not self.required and not value:
            return []
        key = self.to_field_name or 'pk'
        for pk in value:
            try:
                self.queryset.filter(**{key: pk})
            except ValueError:
                raise ValidationError(self.error_messages['invalid_pk_value'] % pk)
        if self.ftype == 'profiles':
            qs = Profile.objects.filter(**{'%s__in' % key: value})
        elif self.ftype == 'metrics':
            qs = Metrics.objects.filter(**{'%s__in' % key: value})
        else:
            qs = self.queryset.filter(**{'%s__in' % key: value})
        pks = set([force_unicode(getattr(o, key)) for o in qs])
        for val in value:
            if force_unicode(val) not in pks:
                raise ValidationError(self.error_messages['invalid_choice'] % val)
        self.run_validators(value)
        if self.ftype == 'profiles' or self.ftype == 'permissions' or self.ftype == 'metrics':
            return qs
        else:
            return qs[0]

    def label_from_instance(self, obj):
        return str(obj.name)

    def _has_changed(self, initial, data):
        initial_value = initial if initial is not None else ''
        data_value = data if data is not None else ''
        return force_text(self.prepare_value(initial_value)) != force_text(data_value)

class MySelect(SelectMultiple):
    allow_multiple_selected = False

    def render(self, name, value, attrs=None, choices=()):
        self.selformname = name
        if value is None: value = ''
        final_attrs = self.build_attrs(attrs, name=name)
        output = [u'<select%s>' % flatatt(final_attrs)]
        options = self.render_options(choices, [value])
        if options:
            output.append(options)
        output.append(u'</select>')
        return mark_safe(u'\n'.join(output))

    def render_options(self, choices, selected_choices):
        sel = force_unicode(selected_choices)
        output = []
        for option_value, option_label in chain(self.choices, choices):
            if isinstance(option_label, (list, tuple)):
                output.append(u'<optgroup label="%s">' % escape(force_unicode(option_value)))
                for option in option_label:
                    output.append(self.render_option(selected_choices, *option))
                output.append(u'</optgroup>')
            else:
                output.append(self.render_option(selected_choices, option_value, option_label))
        if self.selformname == 'permissions':
            for o in output:
                if sel.strip('[]') in o:
                    ind = output.index(o)
                    o = o.replace('value=\"%s\"' % (sel.strip('[]')), 'value=\"%s\" selected=\"selected\"' % (sel.strip('[]')))
                    del(output[ind])
                    output.insert(ind, o)
        return u'\n'.join(output)

class MyFilteredSelectMultiple(admin.widgets.FilteredSelectMultiple):
    def render(self, name, value, attrs=None, choices=()):
        self.selformname = name
        return super(MyFilteredSelectMultiple, self).render(name, value, attrs, choices)

    def render_options(self, choices, selected_choices):
        selected_choices = set(force_unicode(v) for v in selected_choices)
        output = []
        if self.selformname == 'profiles':
            for sel in selected_choices:
                output.append('<option value="%s" selected="selected">' % (sel)
                              + str(Profile.objects.get(id=int(sel)).name)
                              + '</option>\n')
        elif self.selformname == 'metrics':
            for sel in selected_choices:
                output.append('<option value="%s" selected="selected">' % (sel)
                              + str(Metrics.objects.get(id=int(sel)).name)
                              + '</option>\n')
        for option_value, option_label in chain(self.choices, choices):
            if isinstance(option_label, (list, tuple)):
                output.append(u'<optgroup label="%s">' % escape(force_unicode(option_value)))
                for option in option_label:
                    output.append(self.render_option(selected_choices, *option))
                output.append(u'</optgroup>')
            else:
                output.append(self.render_option(selected_choices, option_value, option_label))
        return u'\n'.join(filter(lambda x: '----' not in x, output))

class MetricInstanceForm(ModelForm):
    """
    Connects metric instance attributes to autocomplete widget (:py:mod:`poem.widgets`).
    """
    class Meta:
        model = MetricInstance
        exclude = ('vo',)

    metric = make_ajax_field(MetricInstance, 'metric', 'hintsmetrics', \
                             plugin_options = {'minLength' : 2})
    service_flavour = make_ajax_field(ServiceFlavour, 'name', 'hintsserviceflavours', \
                                      plugin_options = {'minLength' : 2})

    def clean_service_flavour(self):
        clean_values = []
        clean_values = check_cache('/poem/admin/lookups/ajax_lookup/hintsserviceflavours', \
                                   ServiceFlavour, 'name')
        form_flavour = self.cleaned_data['service_flavour']
        if form_flavour not in clean_values:
            raise ValidationError("Unable to find flavour %s." % (str(form_flavour)))
        return form_flavour

class MetricInstanceFormRO(MetricInstanceForm):
    metric = CharField(label='Metric', \
                             widget=TextInput(attrs={'readonly' : 'readonly'}))
    service_flavour = CharField(label='Service Flavour', \
                                   widget=TextInput(attrs={'readonly' : 'readonly'}))

class MetricInstanceInline(admin.TabularInline):
    model = MetricInstance
    form = MetricInstanceForm

    def has_add_permission(self, request):
        if request.user.has_perm('poem.groupown_profile'):
            return True
        if request.user.has_perm('poem.readonly_profile') and \
                not request.user.is_superuser:
            self.form = MetricInstanceFormRO
            return False
        else:
            return True

    def has_delete_permission(self, request, obj=None):
        if request.user.has_perm('poem.groupown_profile'):
            return True
        if request.user.has_perm('poem.readonly_profile') and \
                not request.user.is_superuser:
            self.form = MetricInstanceFormRO
            return False
        else:
            return True

    def has_change_permission(self, request, obj=None):
        return True

class GroupOfProfilesInlineForm(ModelForm):
    def __init__(self, *args, **kwargs):
        rquser = SharedInfo()
        self.user = rquser.getuser()
        self.usergroups = self.user.groupsofprofiles.all()
        super(GroupOfProfilesInlineForm, self).__init__(*args, **kwargs)


    qs = poem.models.GroupOfProfiles.objects.all()
    groupofprofiles = MyModelMultipleChoiceField(queryset=qs,
                                       widget=Select(),
                                       help_text='Profile is a member of given group')
    groupofprofiles.empty_label = '----------------'
    groupofprofiles.label = 'Group of profiles'

    def clean_group(self):
        groupsel = self.cleaned_data['groupofprofiles']
        ugid = [f.id for f in self.usergroups]
        if groupsel.id not in ugid and not self.user.is_superuser:
            raise ValidationError("You are not member of group %s." % (str(groupsel)))
        return groupsel

class GroupOfProfilesInlineAdd(ModelForm):
    def __init__(self, *args, **kwargs):
        super(GroupOfProfilesInlineAdd, self).__init__(*args, **kwargs)
        self.fields['group'].help_text = 'Select one of the groups you are member of'
        self.fields['group'].empty_label = None

class GroupOfProfilesInline(admin.TabularInline):
    model = poem.models.GroupOfProfiles.profiles.through
    form = GroupOfProfilesInlineForm
    verbose_name_plural = 'Group of profiles'
    verbose_name = 'Group of profile'
    max_num = 1
    extra = 1
    template = 'admin/edit_inline/stacked-group.html'

    def has_add_permission(self, request):
        return True

    def has_delete_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return True

class GroupOfProfilesInlineAdd(GroupOfProfilesInline):
    form = GroupOfProfilesInlineAdd

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        lgi = request.user.groupofprofiles.all().values_list('id', flat=True)
        kwargs["queryset"] = poem.models.GroupOfProfiles.objects.filter(pk__in=lgi)
        return super(GroupOfProfilesInline, self).formfield_for_foreignkey(db_field, request, **kwargs)

class ProfileForm(ModelForm):
    """
    Connects profile attributes to autocomplete widget (:py:mod:`poem.widgets`). Also
    adds media and does basic sanity checking for input.
    """
    class Meta:
        model = Profile

    name = CharField(help_text='Namespace and name of this profile.',
                           max_length=128,
                           widget=widgets.NamespaceTextInput(
                                           attrs={'maxlength': 128, 'size': 45}),
                           label='Profile name'
                           )
    vo = make_ajax_field(Profile, 'name', 'hintsvo', \
                         help_text='Virtual organization that owns this profile.', \
                         plugin_options = {'minLength' : 2}, label='Virtual organization')
    description = CharField(help_text='Free text description outlining the purpose of this profile.',
                                  widget=Textarea(attrs={'style':'width:480px;height:100px'}))

    def clean_vo(self):
        clean_values = []
        clean_values = check_cache('/poem/admin/lookups/ajax_lookup/hintsvo', VO, 'name')
        form_vo = self.cleaned_data['vo']
        if form_vo not in clean_values:
            raise ValidationError("Unable to find virtual organization %s." % (str(form_vo)))
        return form_vo

class ProfileAdmin(admin.ModelAdmin):
    """
    POEM admin core class that customizes its look and feel.
    """
    class Media:
        css = { "all" : ("/poem_media/css/poem_profile.custom.css",) }

    list_display = ('name', 'vo', 'description')
    list_filter = ('vo',)
    search_fields = ('name', 'vo',)
    fields = ('name', 'vo', 'description')
    inlines = (GroupOfProfilesInline, MetricInstanceInline, )
    exclude = ('version',)
    form = ProfileForm
    actions = None

    def _groupown_turn(self, user, flag):
        perm_prdel = Permission.objects.get(codename='delete_profile')
        try:
            perm_grpown = Permission.objects.get(codename='groupown_profile')
        except Permission.DoesNotExist:
            ct = ContentType.objects.get(app_label='poem', model='profile')
            perm_grpown = Permission.objects.create(codename='groupown_profile',
                                                   content_type=ct,
                                                   name="Group of profile owners")
        if flag == 'add':
            user.user_permissions.add(perm_grpown)
            user.user_permissions.add(perm_prdel)
        elif flag == 'del':
            user.user_permissions.remove(perm_grpown)
            user.user_permissions.remove(perm_prdel)

    def get_form(self, request, obj=None, **kwargs):
        rquser = SharedInfo(request.user)
        if obj:
            try:
                gp = poem.models.GroupOfProfiles.objects.get(profiles__id=obj.id)
                ugis = request.user.groupsofprofiles.all().values_list('id', flat=True)
                if ugis:
                    for ugi in ugis:
                        if ugi == gp.id:
                            self._groupown_turn(request.user, 'add')
                            break
                        else:
                            self._groupown_turn(request.user, 'del')
            except poem.models.GroupOfProfiles.DoesNotExist:
                self._groupown_turn(request.user, 'del')
        elif not request.user.is_superuser:
            self.inlines = (GroupOfProfilesInlineAdd, MetricInstanceInline,)
            self._groupown_turn(request.user, 'add')
        return super(ProfileAdmin, self).get_form(request, obj=None, **kwargs)

    def save_model(self, request, obj, form, change):
        if change and obj.vo:
            obj.metric_instances.update(vo=obj.vo)
        if request.user.has_perm('poem.groupown_profile'):
            obj.save()
            return
        if not request.user.has_perm('poem.readonly_profile') or \
                request.user.is_superuser:
            obj.save()
            return
        else:
            raise PermissionDenied()

    def get_row_css(self, obj, index):
        if not obj.valid:
            return 'row_red red%d' % index
        return ''

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        if request.user.groupsofprofiles.count():
            return True
        else:
            return False

    def has_delete_permission(self, request, obj=None):
        if request.user.has_perm('poem.groupown_profile'):
            return True
        if request.user.has_perm('poem.readonly_profile') and \
                not request.user.is_superuser:
            return False
        else:
            return True

    def has_change_permission(self, request, obj=None):
        return True

admin.site.register(Profile, ProfileAdmin)

class UserProfileForm(ModelForm):
    subject = CharField(widget=TextInput(attrs={'style':'width:500px'}))

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileForm
    can_delete = False
    verbose_name_plural = 'Additional info'

class UserProfileAdmin(UserAdmin):
    class Media:
        css = { "all" : ("/poem_media/css/poem_profile.custom.css",) }

    fieldsets = [(None, {'fields': ['username', 'password']}),
                 ('Personal info', {'fields': ['first_name', 'last_name', 'email']}),
                 ('Permissions', {'fields': ['is_superuser', 'is_active', 'groupsofprofiles', 'groupsofmetrics']})]
    inlines = [UserProfileInline]
    list_filter = ('is_superuser',)
    filter_horizontal = ('user_permissions',)


#admin.site.unregister(User)
admin.site.register(poem.models.CustUser, UserProfileAdmin)

class GroupOfProfilesAdminForm(ModelForm):
    class Meta:
        model = poem.models.GroupOfProfiles
    qs = Permission.objects.filter(codename__startswith='profile')
    permissions = MyModelMultipleChoiceField(queryset=qs,
                                             widget=MySelect,
                                             help_text='Permission given to user members of the group across chosen profiles',
                                             ftype='permissions')
    permissions.empty_label = '----------------'
    qs = Profile.objects.filter(groupofprofiles__id__isnull=True).order_by('name')
    profiles = MyModelMultipleChoiceField(queryset=qs,
                                          required=False,
                                          widget=MyFilteredSelectMultiple('profiles', False), ftype='profiles')

class GroupOfProfilesAdmin(GroupAdmin):
    class Media:
        css = { "all" : ("/poem_media/css/poem_profile.custom.css",) }

    form = GroupOfProfilesAdminForm
    search_field = ()
    filter_horizontal=('profiles',)
    fieldsets = [(None, {'fields': ['name']}),
                 ('Settings', {'fields': ['permissions', 'profiles']})]

class GroupOfMetricsForm(ModelForm):
    class Meta:
        model = poem.models.GroupOfMetrics
    qs = Permission.objects.filter(codename__startswith='metrics')
    permissions = MyModelMultipleChoiceField(queryset=qs,
                                             widget=MySelect,
                                             help_text='Permission given to user members of the group across chosen metrics',
                                             ftype='permissions')
    permissions.empty_label = '----------------'
    qs = Metrics.objects.filter(groupofmetrics__id__isnull=True).order_by('name')
    metrics = MyModelMultipleChoiceField(queryset=qs,
                                         required=False,
                                         widget=MyFilteredSelectMultiple('metrics', False), ftype='metrics')

class GroupOfMetricsAdmin(GroupAdmin):
    class Media:
        css = { "all" : ("/poem_media/css/poem_profile.custom.css",) }

    form = GroupOfMetricsForm
    search_field = ()
    filter_horizontal=('metrics',)
    fieldsets = [(None, {'fields': ['name']}),
                 ('Settings', {'fields': ['permissions', 'metrics']})]


admin.site.unregister(auth.models.Group)
admin.site.register(poem.models.GroupOfProfiles, GroupOfProfilesAdmin)
admin.site.register(poem.models.GroupOfMetrics, GroupOfMetricsAdmin)
