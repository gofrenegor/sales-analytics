import streamlit as st
import pandas as pd
import utils

st.set_page_config(page_title="Аналитика списаний", page_icon="📦", layout="wide")
utils.inject_css()
utils.render_admin_panel()

st.title("📦 Аналитика списаний")

start_date, end_date, prev_start, prev_end = utils.render_date_selector()
df_curr = utils.load_data(start_date, end_date)
df_prev = utils.load_data(prev_start, prev_end)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

has_prev = not df_prev.empty
if not has_prev:
    st.caption("ℹ️ За период сравнения в базе данных нет — динамика не показывается.")

# --- Проверка качества данных: что не распознано ---
with st.expander("🔍 Проверка данных (что не распознано)"):
    other_st = df_curr[df_curr['flow'] == 'Другое'].groupby('status_before')['loss_sum'].sum().sort_values(ascending=False)
    unk_blocks = df_curr[(df_curr['block_group'] == 'Другое')].groupby('block_name')['loss_sum'].sum().sort_values(ascending=False)
    # Списания в блоке 6800 вне оперки — только для сверки, на расчёт не влияет
    chk = df_curr[df_curr['status_block_id'].astype(str).str.split('.').str[0].str.strip().eq('6800')
                  & (df_curr['block_group'] != 'Опер площадь')]
    out_op = df_curr[df_curr['process_stage'].isin(utils.OPERKA_STAGES) & (df_curr['block_group'] != 'Опер площадь')
                     & ~df_curr['reason'].isin(utils.ASSEMBLY_LR)]
    if not out_op.empty:
        st.info("Сортировка / предсортировка / отгрузка вне блока 6800 (в оперку не попали, учтены в своих блоках). По «ID блока перед списанием»:")
        ids = out_op['status_block_id'].astype(str).str.split('.').str[0].str.strip()
        st.dataframe(out_op.groupby(ids)['loss_sum'].sum().sort_values(ascending=False).rename("Сумма (₽)").reset_index()
                     .rename(columns={'status_block_id': 'ID блока'}), hide_index=True, use_container_width=True)
    lr_op = df_curr[df_curr['block_group'] == 'Опер площадь'].groupby('reason')['loss_sum'].sum().sort_values(ascending=False)
    if not lr_op.empty:
        st.info("LR внутри «Опер площадь». Если какой-то из них на деле сборка — добавьте его в ASSEMBLY_LR (utils.py):")
        st.dataframe(lr_op.rename("Сумма (₽)").reset_index().rename(columns={'reason': 'LR'}), hide_index=True, use_container_width=True)
    if other_st.empty and unk_blocks.empty and chk.empty:
        st.success("Все статусы и блоки распознаны.")
    if not other_st.empty:
        st.warning("Статусы вне потоков («Другое»):")
        st.dataframe(other_st.rename("Сумма (₽)").reset_index(), hide_index=True, use_container_width=True)
    if not unk_blocks.empty:
        st.warning("Блоки, которых нет в BLOCK_MAPPING (в группе «Другое»):")
        st.dataframe(unk_blocks.rename("Сумма (₽)").reset_index(), hide_index=True, use_container_width=True)
    if not chk.empty:
        st.info(f"Для сверки: {len(chk)} строк с ID блока 6800 не попали в оперку (статусы: "
                f"{', '.join(sorted(chk['status_before'].unique())[:10])}), сумма {chk['loss_sum'].sum():,.0f} ₽".replace(",", " "))

st.divider()

for k, v in {'view': 'dashboard', 'selected_flow': None, 'selected_group': None, 'selected_block': None}.items():
    st.session_state.setdefault(k, v)

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
    """Группировка по key: сумма, доля, сумма за прошлый период. Сортировка по убыванию суммы."""
    g = dc.groupby(key)['loss_sum'].sum().reset_index().rename(columns={key: 'name'})
    p = dp.groupby(key)['loss_sum'].sum().rename('prev_sum')
    g = g.merge(p, left_on='name', right_index=True, how='left').fillna({'prev_sum': 0})
    total = g['loss_sum'].sum()
    g['pct'] = g['loss_sum'] / total * 100 if total > 0 else 0
    return g.sort_values('loss_sum', ascending=False).reset_index(drop=True)


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
                chip = '<span class="chip chip-n">нет данных для сравнения</span>'
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
    if st.button(label):
        st.session_state.view = target
        st.rerun()


view = st.session_state.view

# ===== 1. ГЛАВНЫЙ ЭКРАН =====
if view == 'dashboard':
    total, qty = df_curr['loss_sum'].sum(), df_curr['loss_qty'].sum()
    delta = utils.get_delta_text(total, qty, df_prev['loss_sum'].sum(), df_prev['loss_qty'].sum()) if has_prev else None
    st.markdown("### Общие списания за период")
    st.metric("Сумма", f"{total:,.2f} ₽".replace(",", " "), delta=delta, delta_color="inverse")
    n_days = (end_date - start_date).days + 1
    if n_days > 1:
        def daily(d, s, last):
            idx = pd.date_range(s, periods=n_days)
            if d.empty:
                return pd.Series(0.0, index=idx).where(idx <= pd.Timestamp(last)).values
            v = d.groupby(d['loss_date'].dt.normalize())['loss_sum'].sum().reindex(idx, fill_value=0)
            return v.where(idx <= pd.Timestamp(last)).values
        trend = pd.DataFrame({"Текущий период": daily(df_curr, start_date, end_date)}, index=range(1, n_days + 1))
        if has_prev:
            trend["Прошлый период"] = daily(df_prev, prev_start, prev_end)
        st.markdown("#### Динамика по дням")
        st.line_chart(trend, height=220, color=["#4C9AFF", "#8b95a5"][:trend.shape[1]])
        st.caption("По горизонтали — номер дня периода.")
    st.markdown("---")
    st.subheader("Разделение по потокам:")

    def flow_delta(name):
        if not has_prev:
            return None
        c, p = sl(df_curr, name), sl(df_prev, name)
        return utils.get_delta_text(c['loss_sum'].sum(), c['loss_qty'].sum(), p['loss_sum'].sum(), p['loss_qty'].sum())

    c1, c2, c3 = st.columns(3)
    for col, key, label, name, code in [(c1, "btn_inbound", "ВХОДЯЩИЙ ПОТОК", "Входящий поток", "in"),
                                        (c2, "btn_outbound", "ИСХОДЯЩИЙ ПОТОК", "Исходящий поток", "out")]:
        with col:
            if st.button(label, use_container_width=True, key=key):
                st.session_state.selected_flow, st.session_state.view = code, 'flow'
                st.rerun()
            st.metric(f"Сумма: {name.lower()}", f"{sl(df_curr, name)['loss_sum'].sum():,.2f} ₽".replace(",", " "),
                      delta=flow_delta(name), delta_color="inverse")
    with c3:
        st.markdown("<div style='height: 38px;'></div>", unsafe_allow_html=True)
        st.metric("Прочие причины (Другое)", f"{sl(df_curr, 'Другое')['loss_sum'].sum():,.2f} ₽".replace(",", " "),
                  delta=flow_delta('Другое'), delta_color="inverse")
    if (df_curr['flow'] == 'Другое').any():
        st.warning("⚠️ Часть списаний попала в «Другое» — см. «Проверку данных» выше.")

# ===== 2. ЭКРАН ПОТОКА =====
elif view == 'flow':
    back("⬅️ Назад на главную", 'dashboard')
    flow = FLOW_NAMES[st.session_state.selected_flow]
    st.subheader(f"Выбран: {flow}")
    dc, dp = sl(df_curr, flow), sl(df_prev, flow)
    if dc.empty:
        st.info("Нет данных по этому потоку за выбранный период.")
    else:
        sum_badge("Общая сумма списаний по потоку:", dc['loss_sum'].sum())
        st.markdown("---")
        st.markdown("### Выберите блок для детализации:")
        render_cards(build(dc, dp, 'block_group'), 'group', 'flow', "от потока")

# ===== 3. ЭКРАН БЛОКА =====
elif view == 'group':
    back("⬅️ Назад к потокам", 'flow')
    flow, group = FLOW_NAMES[st.session_state.selected_flow], st.session_state.selected_group
    st.subheader(f"Блок: {group} ({flow})")
    dc, dp = sl(df_curr, flow, group), sl(df_prev, flow, group)
    if dc.empty:
        st.info("Нет данных по этому блоку.")
    else:
        sum_badge("Общая сумма списаний по блоку:", dc['loss_sum'].sum())
        st.markdown("---")
        by_stage = group == 'Опер площадь'
        st.markdown("### Выберите этап для детализации:" if by_stage else "### Выберите площадку для детализации:")
        render_cards(build(dc, dp, 'process_stage' if by_stage else 'block_name'), 'block_detail', 'group', "от блока")

# ===== 4. ЭКРАН ПЛОЩАДКИ (детализация по LR) =====
elif view == 'block_detail':
    back("⬅️ Назад к площадкам", 'group')
    flow, group, block = FLOW_NAMES[st.session_state.selected_flow], st.session_state.selected_group, st.session_state.selected_block
    col = 'process_stage' if group == 'Опер площадь' else 'block_name'
    dc, dp = sl(df_curr, flow, group), sl(df_prev, flow, group)
    dc, dp = dc[dc[col] == block], dp[dp[col] == block]
    st.subheader(f"{'Этап' if col == 'process_stage' else 'Площадка'}: {block} ({flow}, блок «{group}»)")
    delta = utils.get_delta_text(dc['loss_sum'].sum(), dc['loss_qty'].sum(), dp['loss_sum'].sum(), dp['loss_qty'].sum()) if has_prev else None
    st.metric("Сумма списаний по площадке", f"{dc['loss_sum'].sum():,.2f} ₽".replace(",", " "), delta=delta, delta_color="inverse")
    st.markdown("---")
    st.markdown("#### Детализация по причинам (LR)")
    lr = dc.groupby('reason')[['loss_sum', 'loss_qty']].sum().reset_index()
    lr['Доля'] = lr['loss_sum'] / lr['loss_sum'].sum() * 100 if lr['loss_sum'].sum() > 0 else 0
    lr = lr.sort_values('loss_sum', ascending=False)
    lr.columns = ['Причина (LR)', 'Сумма (₽)', 'Кол-во (шт)', 'Доля (%)']
    st.dataframe(lr, use_container_width=True, hide_index=True,
                 column_config={"Доля (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)})
