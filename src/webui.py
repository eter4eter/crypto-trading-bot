import dash
import dash_core_components as dcc
import dash_html_components as html
from dash.dependencies import Input, Output, State
import pandas as pd
import sqlite3
from pathlib import Path
import flask
from dash.exceptions import PreventUpdate

from functools import wraps
import base64

# Настройки auth
DASH_USERNAME = 'admin'
DASH_PASSWORD = 'passw0rd'  # Замените на свой надёжный пароль

def check_auth(auth):
    if not auth or not auth.startswith('Basic '):
        return False
    try:
        encoded = auth.split(' ', 1)[1].strip()
        decoded = base64.b64decode(encoded).decode('utf-8')
        username, password = decoded.split(':', 1)
        return username == DASH_USERNAME and password == DASH_PASSWORD
    except Exception:
        return False

def dash_auth_middleware(server):
    @server.before_request
    def _basic_auth():
        AUTH_EXEMPT = ['/assets', '/_dash', '/favicon.ico']
        if any(flask.request.path.startswith(path) for path in AUTH_EXEMPT):
            return None
        auth = flask.request.headers.get('Authorization', None)
        if not check_auth(auth):
            return flask.Response(
                'Authentication required', 401,
                {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return None

DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"

def get_orders(pair=None, side=None, date_start=None, date_end=None):
    with sqlite3.connect(DB_PATH) as conn:
        query = "SELECT id, pair_name, symbol, side, quantity, entry_price, take_profit, stop_loss, status, opened_at, closed_at, close_price, pnl, pnl_percent, close_reason FROM orders"
        filters = []
        params = []
        if pair and pair != 'Все':
            filters.append("symbol = ?")
            params.append(pair)
        if side and side != 'Все':
            filters.append("side = ?")
            params.append(side)
        if date_start:
            filters.append("opened_at >= ?")
            params.append(date_start)
        if date_end:
            filters.append("opened_at <= ?")
            params.append(date_end)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY opened_at DESC"
        df = pd.read_sql_query(query, conn, params=params)
    return df

def get_signals(pair=None, action=None):
    with sqlite3.connect(DB_PATH) as conn:
        query = "SELECT id, pair_name, action, dominant_change, target_change, target_price, executed, created_at FROM signals"
        filters = []
        params = []
        if pair and pair != "Все":
            filters.append("pair_name = ?")
            params.append(pair)
        if action and action != "Все":
            filters.append("action = ?")
            params.append(action)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC"
        df = pd.read_sql_query(query, conn, params=params)
    return df

def get_pair_list():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT DISTINCT symbol FROM orders ORDER BY symbol", conn)
    return ['Все'] + df['symbol'].dropna().unique().tolist()

def get_side_list():
    return ['Все', 'Buy', 'Sell']

def get_action_list():
    return ['Все', 'Buy', 'Sell']

# Dash app setup
server = flask.Flask(__name__)
dash_auth_middleware(server)
app = dash.Dash(__name__, server=server)
app.title = "Crypto Trading Bot Dashboard"

app.layout = html.Div([
    html.H1("Crypto Trading Bot Dashboard"),
    dcc.Tabs([
        dcc.Tab(label="Dashboard", children=[
            html.Div(id="summary-div"),
            dcc.Graph(id="pnl-graph"),
            dcc.Graph(id="winrate-graph"),
            dcc.Interval(id="interval-refresh", interval=60*1000, n_intervals=0)
        ]),
        dcc.Tab(label="Deals", children=[
            html.Div([
                dcc.Dropdown(
                    id="pair-filter",
                    options=[{"label": p, "value": p} for p in get_pair_list()],
                    value="Все",
                    clearable=False,
                    style={"width": "200px", "display": "inline-block", "margin-right": "10px"}
                ),
                dcc.Dropdown(
                    id="side-filter",
                    options=[{"label": s, "value": s} for s in get_side_list()],
                    value="Все",
                    clearable=False,
                    style={"width": "120px", "display": "inline-block", "margin-right": "10px"}
                ),
                dcc.DatePickerRange(
                    id='date-picker-orders',
                    min_date_allowed=pd.Timestamp('2023-01-01'),
                    max_date_allowed=pd.Timestamp.now(),
                    start_date=None,
                    end_date=None,
                    clearable=True,
                    style={"display": "inline-block", "margin-right": "10px"}
                )
            ]),
            html.Div(id="orders-table-div"),
            dcc.Interval(id="interval-orders", interval=120*1000, n_intervals=0)
        ]),
        dcc.Tab(label="Signals", children=[
            html.Div([
                dcc.Dropdown(
                    id="pair-filter-signals",
                    options=[{"label": p, "value": p} for p in get_pair_list()],
                    value="Все",
                    clearable=False,
                    style={"width": "180px", "display": "inline-block", "margin-right": "10px"}
                ),
                dcc.Dropdown(
                    id="action-filter-signals",
                    options=[{"label": a, "value": a} for a in get_action_list()],
                    value="Все",
                    clearable=False,
                    style={"width": "120px", "display": "inline-block", "margin-right": "10px"}
                )
            ]),
            html.Div(id="signals-table-div"),
            dcc.Interval(id="interval-signals", interval=180*1000, n_intervals=0)
        ]),
    ])
])

@app.callback(
    [Output("summary-div", "children"), Output("pnl-graph", "figure"), Output("winrate-graph", "figure")],
    [Input("interval-refresh", "n_intervals")]
)
def update_dashboard(_):
    # ... как и ранее ... (вывод собранных данных)
    import pandas as pd, sqlite3
    with sqlite3.connect(DB_PATH) as conn:
        orders = pd.read_sql_query("SELECT pnl, closed_at FROM orders WHERE status='CLOSED' ORDER BY closed_at DESC", conn)
    if orders.empty:
        summary = html.Ul([html.Li("Нет сделок!")])
        fig_pnl = {'data': [], 'layout': {'title': 'P&L по дням'}}
        fig_win = {'data': [], 'layout': {'title': 'Winrate, %'}}
        return summary, fig_pnl, fig_win
    total_trades = len(orders)
    total_pnl = orders["pnl"].sum()
    winrate = (orders["pnl"] > 0).sum() / total_trades * 100
    best_trade = orders["pnl"].max()
    worst_trade = orders["pnl"].min()
    summary = html.Ul([
        html.Li(f"Всего сделок: {total_trades}"),
        html.Li(f"Суммарный P&L: {total_pnl:+.2f} USDT"),
        html.Li(f"Winrate: {winrate:.2f}%"),
        html.Li(f"Лучшая сделка: {best_trade:+.2f} USDT"),
        html.Li(f"Худшая сделка: {worst_trade:+.2f} USDT"),
    ])
    by_day = orders.copy()
    by_day["date"] = pd.to_datetime(by_day["closed_at"]).dt.date
    stats = by_day.groupby("date").agg(
        total_pnl=pd.NamedAgg(column="pnl", aggfunc="sum"),
        trades=pd.NamedAgg(column="pnl", aggfunc="count"),
        profitable=pd.NamedAgg(column="pnl", aggfunc=lambda x: (x>0).sum())
    ).reset_index()
    fig_pnl = {
        'data': [
            {
                'x': stats['date'].astype(str),
                'y': stats['total_pnl'],
                'type': 'bar',
                'name': 'P&L по дням',
            },
        ],
        'layout': {'title': 'P&L по дням'}
    }
    fig_win = {
        'data': [
            {
                'x': stats['date'].astype(str),
                'y': 100.0*stats['profitable']/stats['trades'],
                'type': 'bar',
                'name': 'Winrate',
            },
        ],
        'layout': {'title': 'Winrate, %'}
    }
    return summary, fig_pnl, fig_win

@app.callback(
    Output("orders-table-div", "children"),
    [Input("interval-orders", "n_intervals"),
     Input("pair-filter", "value"),
     Input("side-filter", "value"),
     Input("date-picker-orders", "start_date"),
     Input("date-picker-orders", "end_date")]
)
def update_orders(_, pair, side, start_date, end_date):
    df = get_orders(pair, side, start_date, end_date)
    if df.empty:
        return html.P("Нет сделок за выбранный период.")
    table = html.Table([
        html.Thead(html.Tr([html.Th(col) for col in df.columns])),
        html.Tbody([
            html.Tr([html.Td(str(df.iloc[i][col])) for col in df.columns]) for i in range(len(df))
        ])
    ], style={"font-size": "11px"})
    return table

@app.callback(
    Output("signals-table-div", "children"),
    [Input("interval-signals", "n_intervals"),
     Input("pair-filter-signals", "value"),
     Input("action-filter-signals", "value")]
)
def update_signals(_, pair, action):
    df = get_signals(pair, action)
    if df.empty:
        return html.P("Нет сигналов за выбранный период.")
    table = html.Table([
        html.Thead(html.Tr([html.Th(col) for col in df.columns])),
        html.Tbody([
            html.Tr([html.Td(str(df.iloc[i][col])) for col in df.columns]) for i in range(len(df))
        ])
    ], style={"font-size": "11px"})
    return table

if __name__ == "__main__":
    app.run_server(debug=True, host="0.0.0.0", port=8050)
