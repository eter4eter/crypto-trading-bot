import dash
import dash_core_components as dcc
import dash_html_components as html
from dash.dependencies import Input, Output
import pandas as pd
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"

# Вспомогательные функции для SQL

def get_orders():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT id, pair_name, symbol, side, quantity, entry_price, take_profit, stop_loss, status, opened_at, closed_at, close_price, pnl, pnl_percent, close_reason FROM orders ORDER BY opened_at DESC
            """, conn
        )
    return df

def get_signals():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT id, pair_name, action, dominant_change, target_change, target_price, executed, created_at FROM signals ORDER BY created_at DESC
            """, conn
        )
    return df

def get_stats(days=30):
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            f"""
            SELECT DATE(closed_at) as date, COUNT(*) as trades, SUM(pnl) as total_pnl, AVG(pnl_percent) as avg_pnl_percent, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as profitable FROM orders WHERE status='CLOSED' AND closed_at IS NOT NULL AND closed_at > DATE('now', '-{days} days') GROUP BY DATE(closed_at) ORDER BY DATE(closed_at) DESC
            """, conn
        )
    return df

# Dash app setup

app = dash.Dash(__name__)
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
            html.Div(id="orders-table-div"),
            dcc.Interval(id="interval-orders", interval=120*1000, n_intervals=0)
        ]),
        dcc.Tab(label="Signals", children=[
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
    orders = get_orders()
    stats = get_stats(30)
    total_trades = len(orders)
    total_pnl = orders["pnl"].sum() if not orders.empty else 0
    winrate = (orders["pnl"] > 0).sum() / total_trades * 100 if total_trades else 0
    best_trade = orders["pnl"].max() if not orders.empty else 0
    worst_trade = orders["pnl"].min() if not orders.empty else 0
    summary = html.Ul([
        html.Li(f"Всего сделок: {total_trades}"),
        html.Li(f"Суммарный P&L: {total_pnl:+.2f} USDT"),
        html.Li(f"Winrate: {winrate:.2f}%"),
        html.Li(f"Лучшая сделка: {best_trade:+.2f} USDT"),
        html.Li(f"Худшая сделка: {worst_trade:+.2f} USDT"),
    ])
    # P&L by day
    fig_pnl = {
        'data': [
            {
                'x': stats['date'],
                'y': stats['total_pnl'],
                'type': 'bar',
                'name': 'P&L по дням',
            },
        ],
        'layout': {'title': 'P&L по дням'}
    }
    # Winrate by day
    fig_win = {
        'data': [
            {
                'x': stats['date'],
                'y': 100.0*stats['profitable']/stats['trades'],
                'type': 'bar',
                'name': 'Winrate',
            },
        ],
        'layout': {'title': 'Winrate, %'}
    }
    return summary, fig_pnl, fig_win

@app.callback(Output("orders-table-div", "children"), [Input("interval-orders", "n_intervals")])
def update_orders(_):
    orders = get_orders()
    if orders.empty:
        return html.P("Нет сделок за выбранный период.")
    table = html.Table([
        html.Thead(html.Tr([html.Th(col) for col in orders.columns])),
        html.Tbody([
            html.Tr([html.Td(str(orders.iloc[i][col])) for col in orders.columns]) for i in range(len(orders))
        ])
    ], style={"font-size": "11px"})
    return table

@app.callback(Output("signals-table-div", "children"), [Input("interval-signals", "n_intervals")])
def update_signals(_):
    signals = get_signals()
    if signals.empty:
        return html.P("Нет сигналов за выбранный период.")
    table = html.Table([
        html.Thead(html.Tr([html.Th(col) for col in signals.columns])),
        html.Tbody([
            html.Tr([html.Td(str(signals.iloc[i][col])) for col in signals.columns]) for i in range(len(signals))
        ])
    ], style={"font-size": "11px"})
    return table

if __name__ == "__main__":
    app.run_server(debug=True, host="0.0.0.0", port=8050)
