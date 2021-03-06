import multiprocessing
import os
import sys
import time
import uuid
from datetime import date, timedelta

import dash
import dash_bootstrap_components as dbc
import dash_core_components as dcc
import dash_html_components as html
import dash_table
import flask
from calc import ExecutionInterrupted
from calc.datasets import get_population_for_area
from calc.simulation import simulate_individuals
from calc.utils import generate_cache_key
from common import cache, settings
from common.interventions import INTERVENTIONS
from common.locale import get_active_locale, init_locale
from components.params import register_params_callbacks, render_disease_params
from components.results import register_results_callbacks, render_results
from dash.dependencies import Input, Output, State
from flask import session
from flask_babel import Babel
from flask_babel import lazy_gettext as _
from flask_session import Session
from scenarios import SCENARIOS
from variables import get_variable, reset_variable, set_variable

os.environ['DASH_PRUNE_ERRORS'] = 'False'
os.environ['DASH_SILENCE_ROUTES_LOGGING'] = 'False'

app_kwargs = dict(suppress_callback_exceptions=True)
if settings.URL_PREFIX:
    app_kwargs['routes_pathname_prefix'] = settings.URL_PREFIX

page_title = 'REINA — Realistic Epidemic Model for COVID-19'
page_description = 'Simulate how interventions affect the spread of an COVID-19 epidemic using a realistic agent model.'

meta_tags = [
    {
        'name': 'title',
        'content': page_title,
    }, {
        'name': 'description',
        'description': page_description
    }
]

if settings.BASE_URL:
    meta_tags.append({
        'property': 'og:url',
        'content': settings.BASE_URL
    })
    meta_tags.append({
        'property': 'og:image',
        'content': settings.BASE_URL + 'assets/site.png'
    })
    meta_tags.append({
        'property': 'og:title',
        'content': page_title
    })
    meta_tags.append({
        'property': 'og:description',
        'content': page_description
    })
    meta_tags.append({
        'property': 'twitter:card',
        'content': 'summary_large_image'
    })
    meta_tags.append({
        'property': 'twitter:image:alt',
        'content': 'Three blue and two red radiating circles on dark background'
    })

app_kwargs['meta_tags'] = meta_tags

app = dash.Dash(__name__, **app_kwargs)
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True
app.title = 'REINA — Realistic Epidemic Model for COVID-19'

server = app.server
with server.app_context():
    server.config.from_object('common.settings')
    server.config['BABEL_TRANSLATION_DIRECTORIES'] = 'locale'

    cache.init_app(server)
    sess = Session()
    sess.init_app(server)
    babel = Babel(default_locale='fi')
    babel.init_app(server)
    init_locale(babel)
    register_results_callbacks(app)
    register_params_callbacks(app)


def generate_static_content():
    f = open(os.path.join(os.path.dirname(__file__), 'Docs', 'description.en.md'))
    return dcc.Markdown(children=f.read())


def interventions_to_rows():
    ivs = get_variable('interventions')
    iv_rows = []
    for iv in sorted(ivs, key=lambda x: x[1]):
        for i in INTERVENTIONS:
            if i.type == iv[0]:
                break
        else:
            # FIXME
            continue
        if len(iv) > 2:
            val = iv[2]
        else:
            val = None
        # date=datetime.strptime(iv[1], '%Y-%m-%d').strftime("%d.%m.%y")
        # Should we display formatted date on list? Does it mess with DataTable?
        unit = None
        if i.parameters:
            unit = getattr(i.parameters[0], 'unit', None)
        row = dict(date=iv[1], label=i.label, value=val, name=i.type, unit=unit)
        iv_rows.append(row)
    return iv_rows


def render_region_info():
    from babel.numbers import format_number

    pop = get_population_for_area().sum().sum()

    region_card = dbc.CardBody(
        html.Ul([
            html.Li([
                html.Strong('%s: ' % _("Region Name")),
                html.Span(get_variable('area_name_long'))
            ]),
            html.Li([
                html.Strong('%s: ' % _("Region Population")),
                html.Span(format_number(pop, locale=get_active_locale())),
            ]),
        ]), className="px-5"),

    return region_card


def render_iv_card():
    ivs = interventions_to_rows()
    iv_table = dash_table.DataTable(
        id='interventions-table',
        data=ivs,
        columns=[
            {'name': _('Date'), 'id': 'date'},
            {'name': _('Event'), 'id': 'label'},
            {'name': _('Value'), 'id': 'value', 'editable': True},
            {'name': '', 'id': 'unit'},
        ],
        style_cell={'textAlign': 'left'},
        style_cell_conditional=[
            {
                'if': {'column_id': 'value'},
                'textAlign': 'right'
            }
        ],
        row_deletable=True,
        style_as_list_view=True,
    )

    iv_card = dbc.CardBody([
        html.Div([
            dbc.Form([
                html.Span(_('Add a new event: ')),
                dcc.DatePickerSingle(
                    id='new-intervention-date', display_format='YYYY-MM-DD',
                    first_day_of_week=1,
                    className="mx-3",
                ),
                dcc.Dropdown(
                    id='new-intervention-id',
                    options=[{'label': i.label, 'value': i.type} for i in INTERVENTIONS],
                    style=dict(width="450px"),
                ),
                dbc.Input(
                    id='new-intervention-value', type='number', size='6',
                    style=dict(width="100px"),
                    className="mx-3",
                    placeholder=_('value'),
                ),
                dbc.Button(
                    _("Add"), id='new-intervention-add', color='primary'
                ),
            ], inline=True),
        ], className="mb-3"),
        iv_table,
        html.Div(dbc.Button(
            _('Restore default events'), id='interventions-reset-defaults', color='secondary',
            size='sm', className='mt-3'
        ), className='text-right'),
    ], className="px-5"),

    return iv_card


def generate_content_rows():
    scenarioRows = []
    resultRows = []

    ivs = get_variable('interventions')
    scenario_id = get_variable('preset_scenario')
    for scenario in SCENARIOS:
        if scenario.id == scenario_id:
            break
    else:
        scenario = None
    if scenario is not None:
        scenarioRows.append(dbc.Row([
            dbc.Col([
                html.Strong(scenario.get_name() + ": "),
                html.Span(scenario.get_description()),
            ], className="mb-3")
        ]))

    scenarioRows.append(dbc.Button(
        '⚙ ' + _('Settings'),
        color="link", className="px-0 mb-1",
        id="settings-collapse-button",
    ))

    settingsTabs = dbc.Card([
        dbc.CardHeader(dbc.Tabs(
            [
                dbc.Tab(label=_("Events (%(num)s)", num=len(ivs)), tab_id="iv-tab"),
                dbc.Tab(label=_("Disease Parameters"), tab_id="param-tab"),
                dbc.Tab(label=_("Region Details"), tab_id="region-tab"),
            ],
            id="card-tabs",
            card=True,
            active_tab="iv-tab",
        )),
        dbc.CardBody(html.Div(id="card-content")),
        dbc.CardFooter([
            html.Div(id='simulation-days-placeholder', style=dict(display='none')),
            dbc.Form([
                dbc.FormGroup([
                    dbc.Label(_('Timeframe'), className="mr-3"),
                    dcc.Dropdown(
                        id='simulation-days-dropdown',
                        options=[dict(label=_('%(days)d days', days=x), value=x) for x in (45, 90, 180, 365, 730)],
                        value=get_variable('simulation_days'),
                        searchable=False, clearable=False,
                        style=dict(width='160px'),
                    )
                ], className="mr-3"),
                dbc.Button(_('Run simulation'), id='run-simulation', color='primary')
            ], inline=True)
        ])
    ])

    scenarioRows.append(
        dbc.Collapse(
            settingsTabs,
            id="settings-collapse"
        )
    )
    scenarioRows.append(dbc.Row([
        dbc.Col(id='scenario-details')
    ]))

    resultRows.append(html.H4(_('Outcome'), className="mb-3"))

    resultRows.append(dbc.Row([
        dbc.Col([
            html.Div(id="simulation-results-container")
        ]),
    ], className='mt-4'))

    resultRows.append(dbc.Row([
        dbc.Col([
            html.Div(id='day-details-container')
        ])
    ]))

    rows = [
        html.Div(
            dbc.Container(scenarioRows),
            className="bg-gray-400 pb-4"
        ),
        dbc.Container(
            resultRows,
            className="pt-4"
        ),
    ]
    return rows


def render_page():
    headerRows = []
    settingsRows = []
    contentRows = []
    traffic_alert = None
    if settings.TRAFFIC_WARNING:
        traffic_alert = dbc.Alert("HUOM: Suuren yhtäaikaisen käyttäjämäärän takia palvelu saattaa toimia hitaasti tai osa simulaatiotoiminnallisuuksista on pois käytöstä.", color="danger", className="mt-4 mb-0")

    headerRows.append(dbc.Row([
        dbc.Col([
            html.Div(html.Small([
                dcc.Link("suomi", id='language-link-fi', href=(settings.URL_PREFIX or '/') + 'fi', className="text-light text-uppercase", refresh=False),
                html.Span(" | ", className="text-muted"),
                dcc.Link("English", id='language-link-en', href=(settings.URL_PREFIX or '/') + 'en', className="text-light text-uppercase", refresh=False),
            ]), className="text-right"),
            html.Div(dbc.Badge("v1.1"), className="text-right"),
            html.Img(src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjM2IiBoZWlnaHQ9IjkyIiB2aWV3Qm94PSIwIDAgMjM2IDkyIiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjxyZWN0IHdpZHRoPSIyMzYiIGhlaWdodD0iOTIiIGZpbGw9IiMzNDNBNDAiLz48ZyBzdHlsZT0ibWl4LWJsZW5kLW1vZGU6c29mdC1saWdodCI+PGNpcmNsZSBjeD0iNDUuNSIgY3k9IjQ2LjUiIHI9IjQ1LjUiIGZpbGw9IiNENEVCRkYiLz48L2c+PGcgc3R5bGU9Im1peC1ibGVuZC1tb2RlOnNvZnQtbGlnaHQiPjxjaXJjbGUgY3g9IjE5MC41IiBjeT0iNDYuNSIgcj0iNDUuNSIgZmlsbD0iI0Q0RUJGRiIvPjwvZz48ZyBzdHlsZT0ibWl4LWJsZW5kLW1vZGU6c29mdC1saWdodCI+PGNpcmNsZSBjeD0iNDYiIGN5PSI0NiIgcj0iMzYiIGZpbGw9IiNDQkUyRjYiLz48L2c+PGcgc3R5bGU9Im1peC1ibGVuZC1tb2RlOnNvZnQtbGlnaHQiPjxjaXJjbGUgY3g9IjE5MCIgY3k9IjQ2IiByPSIzNiIgZmlsbD0iI0NCRTJGNiIvPjwvZz48ZyBzdHlsZT0ibWl4LWJsZW5kLW1vZGU6c29mdC1saWdodCI+PGNpcmNsZSBjeD0iNDUuNSIgY3k9IjQ2LjUiIHI9IjI2LjUiIGZpbGw9IiNDMkQ5RUQiLz48L2c+PGcgc3R5bGU9Im1peC1ibGVuZC1tb2RlOnNvZnQtbGlnaHQiPjxjaXJjbGUgY3g9IjE5MC41IiBjeT0iNDYuNSIgcj0iMjYuNSIgZmlsbD0iI0MyRDlFRCIvPjwvZz48ZyBzdHlsZT0ibWl4LWJsZW5kLW1vZGU6c29mdC1saWdodCI+PGNpcmNsZSBjeD0iNDUuNSIgY3k9IjQ2LjUiIHI9IjEzLjUiIGZpbGw9IiNBQUM1REIiLz48L2c+PGcgc3R5bGU9Im1peC1ibGVuZC1tb2RlOnNvZnQtbGlnaHQiPjxlbGxpcHNlIGN4PSIxOTAiIGN5PSI0Ni41IiByeD0iMTQiIHJ5PSIxMy41IiBmaWxsPSIjQUFDNURCIi8+PC9nPjxnIHN0eWxlPSJtaXgtYmxlbmQtbW9kZTpzb2Z0LWxpZ2h0Ij48Y2lyY2xlIGN4PSIxMTgiIGN5PSI0NiIgcj0iNDYiIGZpbGw9IiNGQ0Q4RDgiLz48L2c+PGcgc3R5bGU9Im1peC1ibGVuZC1tb2RlOnNvZnQtbGlnaHQiPjxjaXJjbGUgY3g9IjExOCIgY3k9IjQ2IiByPSIzNiIgZmlsbD0iI0Y3QjlCOSIvPjwvZz48ZyBzdHlsZT0ibWl4LWJsZW5kLW1vZGU6c29mdC1saWdodCI+PGNpcmNsZSBjeD0iMTE3LjUiIGN5PSI0NS41IiByPSIyNi41IiBmaWxsPSIjRUY5QTlBIi8+PC9nPjxnIHN0eWxlPSJtaXgtYmxlbmQtbW9kZTpzb2Z0LWxpZ2h0Ij48ZWxsaXBzZSBjeD0iMTE4IiBjeT0iNDUuNSIgcng9IjE0IiByeT0iMTMuNSIgZmlsbD0iI0UzN0Q3RCIvPjwvZz48L3N2Zz4=",
            className="mb-3"),
            html.H1("REINA", className="font-weight-bold", style=dict(letterSpacing=".2em")),
            html.H6("Realistic Epidemic Interaction Network Agent Model"),
            traffic_alert,
        ], className='mb-4'),
    ], className='mt-4'))

    scenario_id = get_variable('preset_scenario')

    settingsRows.append(dbc.Row([
        dbc.Col([
            html.H4(_('Scenario'), className="mb-3"),
            html.P(_('Forecast of the COVID-19 epidemic: %(name)s', name=get_variable('area_name_long')), className="lead"),
        ], md=12),
    ]))

    settingsRows.append(dbc.Row([
        dbc.Col([
            dbc.Form(dbc.FormGroup([
                dbc.Label(_('Preset'), className="mr-3"),
                dcc.Dropdown(
                id='preset-scenario-selector',
                options=[{'label': s.get_name(), 'value': s.id} for s in SCENARIOS[0:1]],
                value=scenario_id,
                style=dict(width="300px"),
            )],
            ), inline=True)
        ], md=12),
    ]))

    contentRows.append(html.Div(id='main-content-container'))

    stc = generate_static_content()

    return html.Div([
        html.Div(
            dbc.Container(headerRows),
            className="bg-dark text-light py-4"
        ),
        html.Div(
            dbc.Container(settingsRows),
            className="bg-gray-400 pt-4 pb-2"
        ),
        html.Div(contentRows),
        dbc.Jumbotron(
            dbc.Container(stc),
            className="mt-5 mb-0",
            fluid=True,
        )
    ])


def generate_layout():
    return html.Div([
        dcc.Location(id='url'),
        html.Div(render_page(), id='page-content')
    ])


app.layout = generate_layout


@app.callback(
    Output("card-content", "children"),
    [Input("card-tabs", "active_tab")],
)
def tab_content(active_tab):
    if active_tab == "param-tab":
        return render_disease_params()
    if active_tab == "iv-tab":
        return render_iv_card()
    if active_tab == "region-tab":
        return render_region_info()


@app.callback(
    Output("settings-collapse", "is_open"),
    [Input("settings-collapse-button", "n_clicks")],
    [State("settings-collapse", "is_open")],
)
def toggle_iv_collapse(n, is_open):
    if n:
        return not is_open
    return is_open


"""
@app.callback(
    Output('day-details-container', 'children'),
    [Input('population-graph', 'clickData')]
)
def show_day_details(data):
    print(data)
    return html.Div()
"""


@app.callback(
    Output('interventions-table', 'data'),
    [
        Input('interventions-table', 'data_timestamp'),
        Input('interventions-reset-defaults', 'n_clicks'),
        Input('new-intervention-add', 'n_clicks'),
    ], [
        State('interventions-table', 'data'),
        State('new-intervention-date', 'date'),
        State('new-intervention-id', 'value'),
        State('new-intervention-value', 'value'),
    ]
)
def interventions_callback(ts, reset_clicks, add_intervention_clicks, rows, new_date, new_id, new_val):
    ctx = dash.callback_context
    is_reset = False
    changed = False

    if ctx.triggered:
        c_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if reset_clicks is not None and c_id == 'interventions-reset-defaults':
            reset_variable('interventions')
            is_reset = True
        if add_intervention_clicks is not None and c_id == 'new-intervention-add':
            d = date.fromisoformat(new_date)
            sstart = date.fromisoformat(get_variable('start_date'))
            if d < sstart or d > sstart + timedelta(days=get_variable('simulation_days')):
                raise dash.exceptions.PreventUpdate()

            if new_id in ('test-all-with-symptoms',):
                new_val = None
            elif new_id == ('test-with-contact-tracing', 'test-only-severe-symptoms'):
                if new_val > 100 or new_val < 0:
                    raise dash.exceptions.PreventUpdate()
            elif new_id in ('limit-mobility',):
                if new_val > 100 or new_val < 0:
                    raise dash.exceptions.PreventUpdate()
            elif new_id in ('limit-mass-gatherings', 'import-infections', 'build-new-icu-units', 'build-new-hospital-beds'):
                if new_val < 0:
                    raise dash.exceptions.PreventUpdate()
            else:
                raise dash.exceptions.PreventUpdate()

            changed = True
            rows.append(dict(name=new_id, date=d.isoformat(), value=new_val))
        if c_id == 'interventions-table':
            changed = True

    if not is_reset and changed:
        ivs = []
        for row in sorted(rows, key=lambda x: x['date']):
            val = row['value']
            if isinstance(val, str):
                val = int(val)
            ivs.append([row['name'], row['date'], val])

        set_variable('interventions', ivs)

    rows = interventions_to_rows()
    return rows


process_pool = {}


class SimulationThread(multiprocessing.Process):
    def __init__(self, *args, **kwargs):
        self.variables = kwargs.pop('variables')
        super().__init__(*args, **kwargs)
        self.uuid = str(uuid.uuid4())

    def start(self):
        print('%s: start process' % self.uuid)
        super().start()

    def run(self):
        from common import cache

        process_pool[self.ident] = self

        func_hash = generate_cache_key(simulate_individuals, var_store=self.variables)

        self.last_results = None
        print('%s: run process (func hash %s)' % (self.uuid, func_hash))

        def step_callback(df, force=False):
            now = time.time()
            if force or self.last_results is None or now - self.last_results > 0.5:
                print('%s: set results to %s' % (self.uuid, func_hash))
                cache.set('%s-results' % func_hash, df, timeout=30)
                self.last_results = now

            return True

        try:
            df = simulate_individuals(step_callback=step_callback, variable_store=self.variables)
        except ExecutionInterrupted:
            print('%s: process cancelled' % self.uuid)
        else:
            print('%s: computation finished' % self.uuid)
            step_callback(df, force=True)

        cache.set('%s-finished' % func_hash, True)
        print('%s: process finished' % self.uuid)

        del process_pool[self.ident]


@app.callback(
    [
        Output('simulation-output-results', 'children'),
        Output('simulation-output-interval', 'disabled'),
    ], [Input('simulation-output-interval', 'n_intervals')]
)
def update_simulation_results(n_intervals):
    thread_id = session.get('thread_id', None)
    if thread_id is None:
        raise dash.exceptions.PreventUpdate()

    func_hash = generate_cache_key(simulate_individuals)
    cache_key = '%s-results' % func_hash
    df = cache.get(cache_key)
    if df is None:
        print('%s: no results' % func_hash)
        raise dash.exceptions.PreventUpdate()

    if cache.get('%s-finished' % func_hash):
        # When the computation thread is finished, stop polling.
        print('thread finished, disabling')
        disabled = True
    else:
        print('thread not finished, updating')
        disabled = False
    out = render_results(df)
    return [out, disabled]


@app.callback(
    Output('main-content-container', 'children'),
    [
        Input('preset-scenario-selector', 'value'),
    ],
)
def select_scenario(preset_scenario):
    ctx = dash.callback_context
    if ctx.triggered:
        c_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if c_id == 'preset-scenario-selector':
            for s in SCENARIOS:
                if s.id == preset_scenario:
                    s.apply()
                    break
    return generate_content_rows()


@app.callback(
    Output('simulation-results-container', 'children'),
    [
        Input('run-simulation', 'n_clicks'),
        Input('simulation-days-dropdown', 'value'),
    ],
)
def run_simulation_callback(n_clicks, simulation_days):
    print('run simulation (days %d)' % simulation_days)
    print(generate_cache_key(simulate_individuals))
    set_variable('simulation_days', simulation_days)
    if n_clicks:
        set_variable('random_seed', n_clicks)

    df = simulate_individuals(only_if_in_cache=True)
    if df is not None:
        return render_results(df)

    if settings.RESTRICT_TO_PRESET_SCENARIOS:
        return [html.Div('Palvelussa ruuhkaa, osa simulaatiotoiminnallisuuksista on pois käytöstä')]

    existing_thread_id = session.get('thread_id', None)
    if existing_thread_id:
        cache.set('thread-%s-kill' % existing_thread_id, True)

    process = SimulationThread(variables=session.copy())
    session['thread_id'] = process.uuid
    process.start()

    return [
        dcc.Interval(id='simulation-output-interval', interval=500, max_intervals=60),
        html.Div(id='simulation-output-results'),
    ]


@app.callback(
    Output('page-content', 'children'),
    [Input('url', 'pathname')]
)
def url_callback(pathname):
    if settings.URL_PREFIX:
        if pathname.startswith(settings.URL_PREFIX):
            pathname = pathname[len(settings.URL_PREFIX):]
    pathname = pathname or ''
    pathname = pathname.strip('/')
    if pathname in ('en', 'fi'):
        if flask.has_request_context():
            if session.get('language') != pathname:
                session['language'] = pathname
                return render_page()

    raise dash.exceptions.PreventUpdate()


if __name__ == '__main__':
    # Write the process pid to a file for easier profiling with py-spy
    with open('.corona.pid', 'w') as pid_file:
        pid_file.write(str(os.getpid()))
    kwargs = {}
    if '--externally-visible' in sys.argv:
        kwargs['host'] = '0.0.0.0'
    app.run_server(debug=True, port=8123, **kwargs)
