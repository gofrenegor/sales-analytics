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
    st.markdown(f"""<div style="display:flex;align-items:center;margin:10px 0 20px 0;">
    <span style="font-size:16px;font-weight:600;margin-right:15px;">{label}</span>
    <span style="background-color:rgba(0,191,255,0.1);color:#00BFFF;border:1.5px solid #00BFFF;padding:6px 16px;
    border-radius:20px;font-size:24px;font-weight:700;font-family:monospace;">{fmt_rub(value)}</span></div>""",
                unsafe_allow_html=True)


def render_cards(g, next_view, prefix, share_label):
    """Рамка карточки: красная — потери выросли на 20% и больше, зелёная — снизились на 20% и больше."""
    cols = st.columns(4)
    for i, row in g.iterrows():
        growth = (row['loss_sum'] - row['prev_sum']) / row['prev_sum'] * 100 if row['prev_sum'] > 0 else None
        color = "blue"
        if growth is not None and growth >= 20:
            color = "red"
        elif growth is not None and growth <= -20:
            color = "green"
        pct_text = "< 0.1%" if 0 < row['pct'] < 0.1 else f"{row['pct']:.1f}%"
        with cols[i % 4]:
            with st.container(key=f"card-{color}-{prefix}-{i}", border=True):
                st.markdown(f"**{row['name']}**")
                st.markdown(f"### {fmt_rub(row['loss_sum'])}")
                st.caption(f"{pct_text} {share_label}")
                if has_prev:
                    if growth is None:
                        st.caption("к прошлому периоду: нет данных")
                    else:
                        arrow = "🔺" if growth > 0 else "🔻" if growth < 0 else "▫️"
                        st.caption(f"{arrow} {growth:+.1f}% ({row['loss_sum'] - row['prev_sum']:+,.0f} ₽)".replace(",", " "))
                if st.button("Подробнее", key=f"btn_{prefix}_{i}", use_container_width=True):
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
        st.markdown("### Выберите площадку для детализации:")
        render_cards(build(dc, dp, 'block_name'), 'block_detail', 'group', "от блока")

# ===== 4. ЭКРАН ПЛОЩАДКИ (детализация по LR) =====
elif view == 'block_detail':
    back("⬅️ Назад к площадкам", 'group')
    flow, group, block = FLOW_NAMES[st.session_state.selected_flow], st.session_state.selected_group, st.session_state.selected_block
    dc, dp = sl(df_curr, flow, group, block), sl(df_prev, flow, group, block)  # группа учитывается — суммы совпадают с карточкой
    st.subheader(f"Площадка: {block} ({flow}, блок «{group}»)")
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
