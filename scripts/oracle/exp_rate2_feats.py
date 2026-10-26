"""rate2 step 2: point-in-time per-minute (rate) features for every slate row of seasons 2025-2027.

Box scores have no FGA/FTA, so the usage-share model uses production shares (points, rebounds, assists, and
DK points) as the usage proxy, and the pace proxy is team points for + against per game. All features use
only games that tipped strictly before the night's first tip (same cutoff as the slate builder)."""
import os
import numpy as np, pandas as pd
from dfs.data import nba

SCR = os.environ.get("DFS_EXP_SCRATCH", "data/cache/oracle/scratch")
HL = (3, 5, 10, 20, 40)

def ns(x):
    return x.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy()

sl = pd.read_parquet("data/cache/oracle/nba_slates.parquet").drop_duplicates(["slate", "athlete_id"])
box = nba.load_box([2024, 2025, 2026, 2027])
box = box.drop_duplicates(["game_id", "athlete_id"])
reg = box[box["season_type"] == 2]
cutoffs = reg.assign(slate=reg["game_date"].astype(str)).groupby("slate")["start"].min()
pl = box[~box["did_not_play"] & (box["minutes"] > 0)].sort_values("start").copy()
pl["dk"] = nba.dk_points(pl)
pl["ns"] = ns(pl["start"])
own = {a: (g["ns"].to_numpy(), g["minutes"].to_numpy(float), g["dk"].to_numpy(float), g["season"].to_numpy(),
           g["starter"].fillna(False).astype(bool).to_numpy(), g["points"].to_numpy(float), g["rebounds"].to_numpy(float),
           g["assists"].to_numpy(float), g["turnovers"].to_numpy(float))
       for a, g in pl.groupby("athlete_id")}

# team-season pivots (regular season) of minutes, dk, pts, reb, ast per game x athlete
regp = pl[pl["season_type"] == 2]
tl = {}
for (s, t), g in regp.groupby(["season", "team_abbreviation"]):
    order = g.groupby("game_id")["ns"].first().sort_values()
    pv = {k: g.pivot_table(index="game_id", columns="athlete_id", values=k, aggfunc="max").reindex(order.index).fillna(0.0)
          for k in ("minutes", "dk", "points", "rebounds", "assists")}
    tl[(s, t)] = (order.to_numpy(), pv)
# team game points for / against (pace + defence proxy), and DK allowed per position group
tg = regp.groupby(["season", "game_id", "team_abbreviation"]).agg(pts=("points", "sum"), dk=("dk", "sum"), ns=("ns", "first"),
                                                                  opp=("opponent_team_abbreviation", "first")).reset_index()
tg = tg.merge(tg[["game_id", "team_abbreviation", "pts", "dk"]].rename(columns={"team_abbreviation": "opp", "pts": "opp_pts", "dk": "opp_dk"}),
              on=["game_id", "opp"], how="left")
tg["tot"] = tg["pts"] + tg["opp_pts"]
tgd = {(s, t): (g["ns"].to_numpy(), g["tot"].to_numpy(float), g["opp_dk"].to_numpy(float), g["dk"].to_numpy(float))
       for (s, t), g in tg.sort_values("ns").groupby(["season", "team_abbreviation"])}

def grp(p):
    p = str(p or "")
    ps = p.split(",")
    return "C" if "C" in ps else "G" if ("PG" in ps or "SG" in ps or "G" in ps) else "F"
posg = box.sort_values("start").groupby("athlete_id")["athlete_position_abbreviation"].first().map(grp).to_dict()
# opp DK allowed per minute by position group: for each team-game, DK and minutes scored by the opponents' group
pl["grp"] = pl["athlete_id"].map(posg)
og = regp.assign(grp=regp["athlete_id"].map(posg)).groupby(["season", "game_id", "opponent_team_abbreviation", "grp"]).agg(
    dk=("dk", "sum"), mn=("minutes", "sum"), ns=("ns", "first")).reset_index()
ogd = {(s, t, q): (g["ns"].to_numpy(), np.cumsum(g["dk"].to_numpy(float)), np.cumsum(g["mn"].to_numpy(float)))
       for (s, t, q), g in og.sort_values("ns").groupby(["season", "opponent_team_abbreviation", "grp"])}
lg_g = {}
for (s, q), g in og.groupby(["season", "grp"]):
    lg_g[(s, q)] = (np.sort(g["ns"].to_numpy()), g.sort_values("ns")["dk"].cumsum().to_numpy(), g.sort_values("ns")["mn"].cumsum().to_numpy())

def cum_before(arrs, cutoff):
    n = int(np.searchsorted(arrs[0], cutoff, "left"))
    return n, (arrs[1][n - 1] if n else 0.0), (arrs[2][n - 1] if n else 0.0)

rows = []
for slate, d in sl.groupby("slate", sort=True):
    if slate not in cutoffs.index:
        continue
    cutoff = int(pd.Timestamp(cutoffs[slate]).tz_convert("UTC").value)
    season = int(d["season"].iloc[0])
    # league totals per game (pace proxy baseline)
    lt = tg[(tg["season"] == season) & (tg["ns"] < cutoff)]
    lg_tot = lt["tot"].mean() if len(lt) > 20 else tg[tg["season"] == season - 1]["tot"].mean()
    team_ctx = {}
    for t, g in d.groupby("team"):
        pool = set(g["athlete_id"])
        tv = tl.get((season, t))
        ctx = dict(n=0)
        if tv is not None:
            n = int(np.searchsorted(tv[0], cutoff, "left"))
            if n >= 1:
                pv = {k: v.iloc[:n] for k, v in tv[1].items()}
                mins = pv["minutes"]
                gp = (mins > 0).sum()
                avgm = mins.sum() / gp.clip(lower=1)
                last10 = (mins.iloc[-10:] > 0).mean()
                regs = [b for b in mins.columns if gp[b] >= 3 and avgm[b] >= 15]
                absent = [b for b in regs if b not in pool]
                wt = {b: float(last10[b]) for b in absent}  # recently-active absentees matter (long-term ones are in the rate)
                per_g = {k: v.sum() / gp.clip(lower=1) for k, v in pv.items()}
                ctx = dict(n=n, pv=pv, gp=gp, absent=absent, wt=wt, per_g=per_g, pool=pool, avgm=avgm)
                # remaining (pool) production per game, by stat, among players in tonight's pool with history
                act = [b for b in mins.columns if b in pool]
                ctx["act_tot"] = {k: float(sum(per_g[k][b] * last10[b] for b in act)) + 1e-6 for k in ("points", "rebounds", "assists", "dk")}
                ctx["vac"] = {k: float(sum(per_g[k][b] * wt[b] for b in absent)) for k in ("points", "rebounds", "assists", "dk")}
                ctx["vac_all"] = {k: float(sum(per_g[k][b] for b in absent)) for k in ("dk",)}
                ctx["team_dk_pg"] = float(pv["dk"].sum(axis=1).mean())
        tt = tgd.get((season, t))
        if tt is not None:
            m = int(np.searchsorted(tt[0], cutoff, "left"))
            ctx["tot_pg"] = (tt[1][:m].sum() + 10 * lg_tot) / (m + 10)
        team_ctx[t] = ctx
    for _, r in d.iterrows():
        a, t, o = r["athlete_id"], r["team"], r["opp"]
        f = {"slate": slate, "athlete_id": a}
        q = grp(r["pos"]); f["grp"] = {"G": 0, "F": 1, "C": 2}[q]
        # recency-weighted rates
        ow = own.get(a)
        if ow is not None:
            n = int(np.searchsorted(ow[0], cutoff, "left"))
            if n:
                st, mn, dk, se, sr, pts, reb, ast, tov = (x[:n] for x in ow)
                st, mn, dk, se, sr = st[-82:], mn[-82:], dk[-82:], se[-82:], sr[-82:]
                age = np.arange(len(mn))[::-1]
                for h in HL:
                    w = 0.5 ** (age / h)
                    f[f"rw_dk_{h}"], f[f"rw_min_{h}"] = float((w * dk).sum()), float((w * mn).sum())
                cur = se == season
                f["cur_min"], f["cur_dk"] = float(mn[cur].sum()), float(dk[cur].sum())
                f["cur_n"] = int(cur.sum())
                if cur.any():
                    f["start_share"] = float(sr[cur].mean())
                    # starter vs bench rate this season (role effect)
                    for lab, msk in (("st", cur & sr), ("bn", cur & ~sr)):
                        f[f"{lab}_min"], f[f"{lab}_dk"] = float(mn[msk].sum()), float(dk[msk].sum())
                    # rate in high- vs low-minute games (diminishing returns)
                    mc, dc = mn[cur], dk[cur]
                    med = np.median(mc)
                    f["avg_min_cur"] = float(mc.mean())
                    hi = mc > med
                    f["hi_rate"] = float(dc[hi].sum() / max(mc[hi].sum(), 1)) if hi.any() else np.nan
                    f["lo_rate"] = float(dc[~hi].sum() / max(mc[~hi].sum(), 1))
                # own production shares per minute (usage proxy)
                p2, r2, a2, t2, m2 = pts[-20:], reb[-20:], ast[-20:], tov[-20:], ow[1][:n][-20:]
                f["pts_pm"], f["reb_pm"], f["ast_pm"], f["tov_pm"] = (float(x.sum() / m2.sum()) for x in (p2, r2, a2, t2))
        # teammates-out: WOWY and usage redistribution
        c = team_ctx.get(t, {})
        if c.get("n", 0) >= 1 and a in c["pv"]["minutes"].columns:
            pv = c["pv"]; ma = pv["minutes"][a].to_numpy(); da = pv["dk"][a].to_numpy()
            playd = ma > 0
            tot_m, tot_d = ma[playd].sum(), da[playd].sum()
            rate_all = tot_d / tot_m if tot_m > 0 else np.nan
            ws, wsum, wmax = 0.0, 0.0, 0.0
            joint = playd.copy()
            for b in c["absent"]:
                mb = pv["minutes"][b].to_numpy()
                wo = playd & ~(mb > 0)
                mwo = ma[wo].sum()
                if mwo > 0 and tot_m > 0:
                    dl = (da[wo].sum() / mwo - rate_all) * mwo / (mwo + 100.0)
                    ws += dl * c["wt"][b]; wsum += dl
                    wmax = max(wmax, dl * c["wt"][b])
                joint &= ~(mb > 0)
            f["wowy_w"], f["wowy_sum"], f["wowy_max"] = ws, wsum, wmax
            mj = ma[joint].sum()
            f["wowy_joint"] = ((da[joint].sum() / mj - rate_all) * mj / (mj + 100.0)) if (mj > 0 and len(c["absent"])) else 0.0
            f["n_absent"], f["absent_w"] = len(c["absent"]), float(sum(c["wt"].values()))
            # usage redistribution: vacated (recently active) production split by tonight's pool production shares
            red = 0.0
            for k, wk in (("points", 1.0), ("rebounds", 1.25), ("assists", 1.5)):
                own_pg = float(c["per_g"][k].get(a, 0.0))
                red += wk * c["vac"][k] * own_pg / c["act_tot"][k]
            f["redist_dk_pg"] = red
            f["vac_dk_share"] = c["vac"]["dk"] / max(c["team_dk_pg"], 1.0)
            f["vac_dk_all_share"] = c["vac_all"]["dk"] / max(c["team_dk_pg"], 1.0)
            f["own_dk_share"] = float(c["per_g"]["dk"].get(a, 0.0)) / max(c["team_dk_pg"], 1.0)
        # environment: pace proxy (points for+against) for both teams; opp DK allowed per minute to this group
        ct, co = team_ctx.get(t, {}), None
        tto = tgd.get((season, o))
        if tto is not None:
            m = int(np.searchsorted(tto[0], cutoff, "left"))
            oto = (tto[1][:m].sum() + 10 * lg_tot) / (m + 10)
        else:
            oto = lg_tot
        f["pace_rel"] = (0.5 * (ct.get("tot_pg", lg_tot) + oto)) / lg_tot
        od = ogd.get((season, o, q)); lgq = lg_g.get((season, q))
        if od is not None and lgq is not None:
            _, odk, omn = cum_before(od, cutoff)
            _, ldk, lmn = cum_before(lgq, cutoff)
            lrate = ldk / lmn if lmn > 0 else 1.0
            f["opp_pos_rel"] = ((odk + 400 * lrate) / (omn + 400)) / lrate if lmn > 0 else 1.0
        else:
            f["opp_pos_rel"] = 1.0
        rows.append(f)
out = pd.DataFrame(rows)
out.to_parquet(f"{SCR}/rate2/rate_feats.parquet", index=False)
print(out.shape); print(out.describe().T.to_string())
