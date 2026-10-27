from html import escape

import streamlit as st
import pandas as pd
import altair as alt
import utils

st.set_page_config(page_title="Аналитика списаний", page_icon="📦", layout="wide")
utils.inject_css()
utils.render_admin_panel()
MONEY = {"Сумма (₽)": st.column_config.NumberColumn(format="%.0f ₽")}  # рубли без копеек

st.title("📦 Аналитика списаний")

start_date, end_date, prev_start, prev_end = utils.render_date_selector()
df_curr = utils.load_data(start_date, end_date)
df_prev = utils.load_data(prev_start, prev_end)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

# Статусы вне потоков («Другое») временно исключены из итогов и экранов — показываются отдельной строкой
other_curr, other_prev = df_curr[df_curr['flow'] == 'Другое'], df_prev[df_prev['flow'] == 'Другое']
df_curr, df_prev = df_curr[df_curr['flow'] != 'Другое'], df_prev[df_prev['flow'] != 'Другое']
has_prev = not df_prev.empty
if not has_prev:
    st.caption("ℹ️ За период сравнения в базе данных нет — динамика не показывается.")

# --- Проверка качества данных: что не распознано ---
with st.expander("🔍 Проверка данных (что не распознано)"):
    other_st = other_curr.groupby('status_before')['loss_sum'].sum().sort_values(ascending=False)
    unk_blocks = df_curr[df_curr['block_group'] == utils.UNMAPPED_GROUP].groupby('block_name')['loss_sum'].sum().sort_values(ascending=False).head(15)
    # Списания в блоке 6800 вне оперки — только для сверки, на расчёт не влияет
    chk = df_curr[df_curr['status_block_id'].astype(str).str.split('.').str[0].str.strip().eq('6800')
                  & (df_curr['block_group'] != 'Опер площадь') & ~df_curr['reason'].isin(utils.ASSEMBLY_LR)]
    lr_op = df_curr[df_curr['block_group'] == 'Опер площадь'].groupby('reason')['loss_sum'].sum().sort_values(ascending=False)
    if not lr_op.empty:
        st.info("LR внутри «Опер площадь». Если какой-то из них на деле сборка — добавьте его в ASSEMBLY_LR (utils.py):")
        st.dataframe(lr_op.rename("Сумма (₽)").reset_index().rename(columns={'reason': 'LR'}), hide_index=True, use_container_width=True, column_config=MONEY)
    if other_st.empty and unk_blocks.empty and chk.empty:
        st.success("Все статусы и блоки распознаны.")
    if not other_st.empty:
        st.warning("Статусы вне потоков — временно исключены из итогов:")
        tbl = other_curr.groupby(['status_before', 'reason'])['loss_sum'].sum().sort_values(ascending=False).head(15)
        st.dataframe(tbl.rename("Сумма (₽)").reset_index().rename(columns={'status_before': 'Статус', 'reason': 'LR'}),
                     hide_index=True, use_container_width=True, column_config=MONEY)
    if not unk_blocks.empty:
        st.warning("Блоки нашего ЛО без маппинга (учтены в группе «Блоки вне маппинга»; топ-15). Добавьте их в BLOCK_MAPPING (utils.py):")
        st.dataframe(unk_blocks.rename("Сумма (₽)").reset_index(), hide_index=True, use_container_width=True, column_config=MONEY)
    if not chk.empty:
        st.info(f"Для сверки: {len(chk)} строк с ID блока 6800 не попали в оперку (статусы: "
                f"{', '.join(sorted(chk['status_before'].unique())[:10])}), сумма {chk['loss_sum'].sum():,.0f} ₽".replace(",", " "))

st.divider()

for k, v in {'view': 'dashboard', 'selected_flow': None, 'selected_group': None, 'selected_block': None}.items():
    st.session_state.setdefault(k, v)

_flow_qp = st.query_params.get("flow")
if _flow_qp in ("in", "out"):
    st.session_state.selected_flow = _flow_qp
    st.session_state.selected_group = None
    st.session_state.selected_block = None
    st.session_state.view = "flow"
    st.session_state.pop("loss_donut_click", None)
    del st.query_params["flow"]
    st.rerun()

FLOW_NAMES = {"in": "Входящий поток", "out": "Исходящий поток"}
fmt_rub = lambda x: f"{x:,.0f} ₽".replace(",", " ")


def sl(df, flow=None, group=None, block=None):
    """Срез данных по потоку / блоку / площадке."""
    if flow is not None:
        df = df[df['flow'] == flow]
    if group is not None:
        df = df[df['block_group'] == group]
    if block is not None:
        df = df[df['block_name'] == block]
    return df


def build(dc, dp, key):
    """Группировка по key: сумма, доля, сумма за прошлый период (включая позиции, где сейчас 0, а раньше были потери)."""
    g = pd.concat([dc.groupby(key)['loss_sum'].sum().rename('loss_sum'),
                   dp.groupby(key)['loss_sum'].sum().rename('prev_sum')], axis=1).fillna(0)
    g.index.name = 'name'
    g = g.reset_index()
    total = g['loss_sum'].sum()
    g['pct'] = g['loss_sum'] / total * 100 if total > 0 else 0.0
    return g.sort_values('loss_sum', ascending=False).reset_index(drop=True)


def ensure(g, names):
    """Добавляет нулевые карточки для позиций, которых нет в данных, — структура видна всегда."""
    miss = [n for n in names if n not in set(g['name'])]
    if miss:
        g = pd.concat([g, pd.DataFrame({'name': miss, 'loss_sum': 0.0, 'prev_sum': 0.0, 'pct': 0.0})], ignore_index=True)
    return g.sort_values('loss_sum', ascending=False, kind='stable').reset_index(drop=True)


def sum_badge(label, value):
    st.markdown(f'<div class="sub">{label}</div><div class="big-sum">{fmt_rub(value)}</div>', unsafe_allow_html=True)


def render_cards(g, next_view, prefix, share_label):
    """Рамка: красная — потери выросли на 20% и больше, зелёная — снизились на 20% и больше."""
    cols = st.columns(4)
    for i, row in g.iterrows():
        diff = row['loss_sum'] - row['prev_sum']
        growth = diff / row['prev_sum'] * 100 if row['prev_sum'] > 0 else None
        color = "red" if growth is not None and growth >= 20 else "green" if growth is not None and growth <= -20 else "plain"
        chip = ""
        if has_prev:
            if growth is None:
                chip = f'<span class="chip chip-n">{"нет списаний" if row["loss_sum"] == 0 else "нет данных для сравнения"}</span>'
            else:
                cls, arrow = ("chip-r", "▲") if growth > 0 else ("chip-g", "▼") if growth < 0 else ("chip-n", "•")
                chip = f'<span class="chip {cls}">{arrow} {abs(growth):.1f}% · {diff:+,.0f} ₽</span>'.replace(",", " ")
        pct_text = "< 0.1%" if 0 < row['pct'] < 0.1 else f"{row['pct']:.1f}%"
        with cols[i % 4]:
            with st.container(key=f"card-{color}-{prefix}-{i}", border=True):
                st.markdown(f'<div class="c-name">{row["name"]}</div><div class="c-sum">{fmt_rub(row["loss_sum"])}</div>{chip}'
                            f'<div class="bar"><div style="width:{min(row["pct"], 100):.1f}%"></div></div>'
                            f'<div class="c-share">{pct_text} {share_label}</div>', unsafe_allow_html=True)
                if st.button("Открыть →", key=f"btn_{prefix}_{i}", use_container_width=True):
                    if next_view == 'group':
                        st.session_state.selected_group = row['name']
                    else:
                        st.session_state.selected_block = row['name']
                    st.session_state.view = next_view
                    st.rerun()


def back(label, target):
    if st.button(label, key="nav_back"):
        st.session_state.view = target
        st.session_state.pop("loss_donut_click", None)
        st.rerun()


def open_flow(code):
    """Строка потока на главной ведёт на выбор блоков этого потока."""
    st.session_state.selected_flow = code
    st.session_state.selected_group = None
    st.session_state.selected_block = None
    st.session_state.view = "flow"
    st.rerun()


def render_flow_hero(in_sum, out_sum):
    """Кольцо потерь и два кликабельных потока справа."""
    chart, side = st.columns([1.25, 1], vertical_alignment="center", gap="large")
    with chart:
        st.markdown(
            utils.build_loss_donut_svg(in_sum, out_sum, fmt_rub(in_sum + out_sum)),
            unsafe_allow_html=True,
        )
    with side:
        for code, title, amount, key in (
            ("in", "входящий поток", in_sum, "pick_in"),
            ("out", "исходящий поток", out_sum, "pick_out"),
        ):
            help_text = "Открыть блоки входящего потока" if code == "in" else "Открыть блоки исходящего потока"
            if st.button(f"{title}  **{fmt_rub(amount)}**", key=key, width="stretch", help=help_text):
                open_flow(code)
        st.caption("Клик по сегменту кольца или строке потока откроет выбор блоков.")


view = st.session_state.view

# ===== 1. ГЛАВНЫЙ ЭКРАН =====
if view == 'dashboard':
    in_sum = sl(df_curr, "Входящий поток")["loss_sum"].sum()
    out_sum = sl(df_curr, "Исходящий поток")["loss_sum"].sum()
    render_flow_hero(in_sum, out_sum)
    if has_prev:
        st.markdown(
            '<p class="delta-line">К прошлому периоду · '
            + escape(utils.get_delta_text(
                df_curr["loss_sum"].sum(), df_curr["loss_qty"].sum(),
                df_prev["loss_sum"].sum(), df_prev["loss_qty"].sum()))
            + "</p>",
            unsafe_allow_html=True,
        )
    other_sum = other_curr["loss_sum"].sum()
    if other_sum or not other_prev.empty:
        delta_html = ""
        if not other_prev.empty:
            delta_html = (
                '<div class="aside-delta">'
                + escape(utils.get_delta_text(
                    other_sum, other_curr["loss_qty"].sum(),
                    other_prev["loss_sum"].sum(), other_prev["loss_qty"].sum()))
                + "</div>"
            )
        st.markdown(
            '<div class="aside-metric"><div class="aside-label">Вне потоков '
            '<span>· не входит в диаграмму</span></div>'
            f'<div class="aside-value">{fmt_rub(other_sum)}</div>{delta_html}</div>',
            unsafe_allow_html=True,
        )
    if not other_curr.empty:
        st.caption("Статусы вне потоков временно исключены из диаграммы и итогов — список в «Проверке данных» выше.")
    n_days = (end_date - start_date).days + 1
    if n_days > 1:
        def daily(d, s, last, label):
            idx = pd.date_range(s, periods=n_days)
            top, top_lr, top_blk_lr = {}, {}, {}
            if d.empty:
                sums = qty = pd.Series(0.0, index=idx)
            else:
                day = d['loss_date'].dt.normalize().rename('day')
                sums = d.groupby(day)['loss_sum'].sum().reindex(idx, fill_value=0)
                qty = d.groupby(day)['loss_qty'].sum().reindex(idx, fill_value=0)
                by_blk = d.groupby([day, d['block_group']])['loss_sum'].sum().sort_values(ascending=False)
                top_b = by_blk.groupby(level=0).head(1)
                top = {k[0]: f"{k[1]} · {fmt_rub(v)}" for k, v in top_b.items()}
                by_lr = d.groupby([day, d['reason']])['loss_sum'].sum().sort_values(ascending=False)
                top_lr = {k[0]: f"LR {k[1]} · {fmt_rub(v)}" for k, v in by_lr.groupby(level=0).head(1).items()}
                gl = d.groupby([day, d['block_group'], d['reason']])['loss_sum'].sum().reset_index()
                gl = gl.merge(top_b.reset_index()[['day', 'block_group']], on=['day', 'block_group'])
                gl = gl.sort_values('loss_sum', ascending=False).groupby('day').head(1)
                top_blk_lr = {r.day: f"LR {r.reason} · {fmt_rub(r.loss_sum)}" for r in gl.itertuples()}
            grp = lambda vals: [f"{v:,.0f}".replace(",", " ") for v in vals]
            out = pd.DataFrame({'Период': label, 'День': range(1, n_days + 1), 'Дата': [x.strftime('%d.%m.%Y') for x in idx],
                                'Топ-1 LR': [top_lr.get(x, '—') for x in idx],
                                'Сумма': sums.values, 'Штуки': qty.values,
                                'Сумма, ₽': grp(sums.values), 'Штуки, шт': grp(qty.values),
                                'Топ-1 блок': [top.get(x, '—') for x in idx],
                                'Топ-1 LR в блоке': [top_blk_lr.get(x, '—') for x in idx]})
            return out[idx <= pd.Timestamp(last)]
        parts = [daily(df_curr, start_date, end_date, 'Текущий период')]
        if has_prev:
            parts.append(daily(df_prev, prev_start, prev_end, 'Прошлый период'))
        st.markdown('<h4 class="section-title">Динамика по дням</h4>', unsafe_allow_html=True)
        chart_df = pd.concat(parts)
        x_enc = alt.X('День:Q', title=None, axis=alt.Axis(
            tickMinStep=1, grid=False, labelColor='#7d8794', labelFontSize=12, labelPadding=6,
            domainColor='rgba(128,128,128,0.35)', tickColor='rgba(128,128,128,0.35)',
        ))
        y_enc = alt.Y('Сумма:Q', title=None, axis=alt.Axis(
            tickCount=4, grid=True, gridColor='rgba(128,128,128,0.16)', gridDash=[3, 4],
            labelColor='#7d8794', labelFontSize=12, labelPadding=8, domain=False, ticks=False,
            labelExpr="replace(format(datum.value, ',.0f'), ',', '\\u00a0')",
        ))
        tip = [
            alt.Tooltip('Период:N'), alt.Tooltip('Дата:N'), alt.Tooltip('Топ-1 LR:N'),
            alt.Tooltip('Сумма, ₽:N'), alt.Tooltip('Штуки, шт:N'),
            alt.Tooltip('Топ-1 блок:N'), alt.Tooltip('Топ-1 LR в блоке:N'),
        ]
        color_enc = alt.Color(
            'Период:N',
            scale=alt.Scale(domain=['Текущий период', 'Прошлый период'], range=['#4C9AFF', '#8b95a5']),
            legend=alt.Legend(orient='bottom', title=None, labelFontSize=13, labelColor='#7d8794', symbolStrokeWidth=3, padding=8),
        )
        dash_enc = alt.StrokeDash(
            'Период:N',
            scale=alt.Scale(domain=['Текущий период', 'Прошлый период'], range=[[1, 0], [5, 4]]),
            legend=None,
        )
        area = (
            alt.Chart(chart_df)
            .transform_filter(alt.datum.Период == 'Текущий период')
            .mark_area(color='#4C9AFF', opacity=0.13, interpolate='linear')
            .encode(x=x_enc, y=y_enc, tooltip=tip)
        )
        line = (
            alt.Chart(chart_df)
            .mark_line(strokeWidth=2.25, interpolate='linear', point=alt.OverlayMarkDef(size=36, filled=True))
            .encode(x=x_enc, y=y_enc, color=color_enc, strokeDash=dash_enc, tooltip=tip)
        )
        daily_chart = (
            (area + line)
            .properties(height=400)
            .configure_view(stroke=None)
            .configure(background='transparent', font='Manrope, sans-serif')
        )
        st.altair_chart(daily_chart, use_container_width=True)
        st.caption("По горизонтали — номер дня в периоде. Наведите курсор на точку: топ-1 LR за день, сумма, штуки, топ-1 блок и топ-1 LR внутри этого блока.")

# ===== 2. ЭКРАН ПОТОКА =====
elif view == 'flow':
    back("← Назад на главную", 'dashboard')
    flow = FLOW_NAMES[st.session_state.selected_flow]
    st.subheader(f"Выбран: {flow}")
    dc, dp = sl(df_curr, flow), sl(df_prev, flow)
    sum_badge("Общая сумма списаний по потоку:", dc['loss_sum'].sum())
    st.markdown("---")
    st.markdown("### Выберите блок для детализации:")
    # Вход: физические блоки + «Брак». Исход: физические блоки + оперка. Чужой ЛО в обоих.
    names = utils.ALL_BLOCKS + (
        [utils.DEFECT_GROUP] if flow == 'Входящий поток' else ['Опер площадь']
    ) + [utils.FOREIGN_GROUP]
    render_cards(ensure(build(dc, dp, 'block_group'), names), 'group', 'flow', "от потока")

# ===== 3. ЭКРАН БЛОКА =====
elif view == 'group':
    back("← Назад к потокам", 'flow')
    flow, group = FLOW_NAMES[st.session_state.selected_flow], st.session_state.selected_group
    st.subheader(f"Блок: {group} ({flow})")
    dc, dp = sl(df_curr, flow, group), sl(df_prev, flow, group)
    sum_badge("Общая сумма списаний по блоку:", dc['loss_sum'].sum())
    st.markdown("---")
    if group == 'Опер площадь':
        st.markdown("### Выберите этап для детализации:")
        render_cards(ensure(build(dc, dp, 'process_stage'), ['Сортировка', 'Предсортировка', 'Отгрузка']),
                     'block_detail', 'group', "от блока")
    elif group == utils.PICKMISS_GROUP:
        st.markdown("### Выберите площадку для детализации:")
        g_all = build(dc, dp, 'block_name')
        known = set()
        for bi, b in enumerate(utils.ALL_BLOCKS):  # все 4 блока с площадками — всегда, даже пустые
            known |= set(utils.PLATFORMS[b])
            st.markdown(f"#### {b}")
            render_cards(ensure(g_all[g_all['name'].isin(utils.PLATFORMS[b])], utils.PLATFORMS[b]),
                         'block_detail', f'group{bi}', "от блока")
        rest = g_all[~g_all['name'].isin(known)]
        if not rest.empty:
            st.markdown("#### Другое")
            render_cards(rest.reset_index(drop=True), 'block_detail', 'groupx', "от блока")
    elif group == utils.FOREIGN_GROUP:
        st.markdown("### Выберите статус для детализации:")
        render_cards(build(dc, dp, 'status_before'), 'block_detail', 'group', "от блока")
    elif group == utils.DEFECT_GROUP:
        st.markdown("### Выберите статус для детализации:")
        render_cards(ensure(build(dc, dp, 'status_before'), utils.DEFECT_STATUSES),
                     'block_detail', 'group', "от блока")
    else:
        st.markdown("### Выберите площадку для детализации:")
        render_cards(ensure(build(dc, dp, 'block_name'), utils.PLATFORMS.get(group, [])), 'block_detail', 'group', "от блока")

# ===== 4. ЭКРАН ПЛОЩАДКИ (детализация по LR) =====
elif view == 'block_detail':
    back("← Назад к площадкам", 'group')
    flow, group, block = FLOW_NAMES[st.session_state.selected_flow], st.session_state.selected_group, st.session_state.selected_block
    col = ('process_stage' if group == 'Опер площадь'
           else 'status_before' if group in (utils.FOREIGN_GROUP, utils.DEFECT_GROUP)
           else 'block_name')
    dc, dp = sl(df_curr, flow, group), sl(df_prev, flow, group)
    dc, dp = dc[dc[col] == block], dp[dp[col] == block]
    st.subheader(f"{ {'process_stage': 'Этап', 'status_before': 'Статус'}.get(col, 'Площадка') }: {block} ({flow}, блок «{group}»)")
    delta = utils.get_delta_text(dc['loss_sum'].sum(), dc['loss_qty'].sum(), dp['loss_sum'].sum(), dp['loss_qty'].sum()) if has_prev else None
    st.metric("Сумма списаний по площадке", f"{dc['loss_sum'].sum():,.0f} ₽".replace(",", " "), delta=delta, delta_color="inverse")
    st.markdown("---")
    oc = other_curr[(other_curr['block_group'] == group) & (other_curr[col] == block)]
    if not oc.empty:
        st.caption(f"ℹ️ Не включено: {fmt_rub(oc['loss_sum'].sum())} со статусами вне потоков (какие именно — в «Проверке данных» вверху страницы).")
    st.markdown("#### Детализация по причинам (LR)")
    lr = dc.groupby('reason')[['loss_sum', 'loss_qty']].sum().reset_index()
    lr['Доля'] = lr['loss_sum'] / lr['loss_sum'].sum() * 100 if lr['loss_sum'].sum() > 0 else 0
    lr = lr.sort_values('loss_sum', ascending=False)
    lr.columns = ['Причина (LR)', 'Сумма (₽)', 'Кол-во (шт)', 'Доля (%)']
    st.dataframe(lr, use_container_width=True, hide_index=True,
                 column_config={**MONEY, "Доля (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)})
