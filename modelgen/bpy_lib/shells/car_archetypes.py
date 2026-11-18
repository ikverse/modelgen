"""Example car sheets: hand-tuned starting points for common kinds of car, each checked by building and
rendering it. The car architect starts from the closest one and changes only what this car needs.

Plain Python with no Blender import. Each entry has a `description` (what it looks like), a `sheet`
(a full car sheet, see car_sheet) and `accessories` (as the car architect submits them). Each has its
own face and tail: the headlight shape and placement, grille, intakes, tail lamps, plate and vents
that suit its kind, so no two look alike.
"""

ARCHETYPES = {
    "city_car": dict(
        description="Small upright city hatchback (3.6 m): short rounded nose, tall glasshouse, big upright oval "
                    "headlights, a small grille slot, short vertical tail lamps, plate on the tailgate.",
        sheet=dict(style="hatch", length=3.6, width=1.65, height=1.5, wheelbase=2.4, front_overhang=0.6,
                   wheel_diameter=0.58, wheel_width=0.175, track=1.42, ride_height=0.13, nose_z=0.74, cowl_z=0.98,
                   windshield_base=-0.98, roof_front=-0.32, roof_rear=1.2, deck_front=1.62, deck_z=1.04, tail_z=1.0,
                   belt_z=0.98, max_width_z=0.6, roof_width=1.24, hood_curve=1.8, nose_taper=0.86, tail_taper=0.92,
                   nose_edge_radius=0.08, nose_rake=0.12, lamp_shape="oval", lamp_span=0.36, lamp_height=0.14,
                   lamp_drop=0.10, grille="small", intake="center", tail_lamp_style="vertical",
                   tail_lamp_height=0.3, plate_rear="panel"),
        accessories=dict(wheel=dict(spokes=4, style="disc"), exhaust=None)),
    "hot_hatch": dict(
        description="Sporty five-door hatchback (4.26 m): low squared-off nose, crisp angular panels, thin angular "
                    "headlights joined by a light bar, split side intakes, boxed flares, lip and skirts, wraparound "
                    "tail lamps, diffuser, twin exhaust.",
        sheet=dict(style="hatch", length=4.29, width=1.8, height=1.46, wheelbase=2.63, front_overhang=0.88,
                   wheel_diameter=0.64, wheel_width=0.225, track=1.54, ride_height=0.14, nose_z=0.74, cowl_z=1.00,
                   windshield_base=-0.865, roof_front=0.0, roof_rear=1.62, deck_front=1.90, deck_z=1.06,
                   tail_z=1.00, nose_peak_z=0.45, nose_rollover=0.15, nose_tuck=0.15, belt_z=0.97, look="angular", flare_style="boxed", flare=0.045, front_lip=0.03,
                   skirt=0.03, vents=["hood_vents"], nose_round=3.0, nose_rake=0.0, lamp_shape="angular",
                   lamp_height=0.075, lamp_drop=0.08, lamp_bar=True, grille="slot", intake="split",
                   tail_lamp_style="wrap", plate_rear="panel", diffuser=True),
        accessories=dict(wheel=dict(spokes=5, style="spoke"), exhaust=dict(count=2, spacing=0.12))),
    "family_sedan": dict(
        description="Mid-size four-door saloon (4.85 m): sloped nose, smooth panels, three-box profile with a short "
                    "low boot, long swept headlights beside a wide chrome-framed grille, tail lamps split across the "
                    "boot lid, plate on the boot face, mesh wheels.",
        sheet=dict(style="sedan", length=4.88, width=1.84, height=1.445, wheelbase=2.825, front_overhang=0.98,
                   wheel_diameter=0.67, wheel_width=0.225, track=1.58, ride_height=0.15, nose_z=0.74, cowl_z=1.00,
                   windshield_base=-0.96, roof_front=-0.09, roof_rear=0.92, deck_front=1.83, deck_z=1.04, tail_z=1.00,
                   belt_z=1.00, nose_peak_z=0.45, nose_rollover=0.15, nose_tuck=0.18, tail_rake=0.04, nose_rake=0.0, nose_round=2.6, lamp_shape="swept", lamp_span=0.42,
                   lamp_drop=0.09, grille="wide", intake="center", tail_lamp_style="split", plate_rear="panel"),
        accessories=dict(wheel=dict(spokes=10, style="mesh"))),
    "jdm_coupe": dict(
        description="1990s Japanese sports coupe (4.52 m): low wedge nose with pop-up headlights and no upper grille, "
                    "a wide lower mouth, angular body with boxed flares, round twin tail lamps, diffuser, rear wing.",
        sheet=dict(style="coupe", length=4.52, width=1.75, height=1.29, wheelbase=2.6, front_overhang=0.92,
                   wheel_diameter=0.64, wheel_width=0.225, track=1.48, ride_height=0.12, nose_z=0.58, cowl_z=0.88,
                   windshield_base=-1.02, roof_front=-0.30, roof_rear=0.62, deck_front=1.55, deck_z=0.98,
                   tail_z=0.92, belt_z=0.9, max_width_z=0.52, roof_width=1.16, nose_point=0.22, nose_round=2.4,
                   lamp_style="popup", grille="none", intake="full", tail_lamp_style="round_twin", diffuser=True),
        accessories=dict(wheel=dict(spokes=6), wing=dict(span=1.5, chord=0.24, height=0.22))),
    "muscle_coupe": dict(
        description="American muscle coupe (4.8 m): long flat hood with a bulge, blunt flat upright nose, twin round "
                    "headlights set deep beside a tall chrome-framed grille, set-back cabin, big wheels, square "
                    "cut-off tail with three vertical lamp bars a side, twin exhaust.",
        sheet=dict(style="coupe", length=4.8, width=1.9, height=1.36, wheelbase=2.8, front_overhang=1.0,
                   wheel_diameter=0.72, wheel_width=0.255, track=1.6, ride_height=0.13, nose_z=0.82, cowl_z=0.96,
                   windshield_base=-0.85, roof_front=-0.15, roof_rear=0.75, deck_front=1.72, deck_z=1.03,
                   tail_z=1.01, belt_z=0.95, max_width_z=0.58, roof_width=1.24, nose_rake=0.0, tail_rake=0.01,
                   nose_round=4.0, tail_round=4.0, tail_edge_radius=0.02,
                   hood_curve=1.0, flare_style="soft", flare=0.05, look="angular", rear_fender_rise=0.04, hip_length=0.45,
                   vents=["hood_vents"], lamp_shape="round", round_count=2, lamp_span=0.38, lamp_height=0.12,
                   lamp_drop=0.11, grille="tall", intake="low", tail_lamp_style="tri"),
        accessories=dict(wheel=dict(spokes=5), exhaust=dict(count=2, spacing=0.14))),
    "supercar": dict(
        description="Low mid-engine supercar (4.55 m, 1.16 m tall): pointed low nose with thin blade headlights and "
                    "no upper grille, split side intakes, cab-forward canopy, long door, side glass ending behind it, "
                    "one straight engine cover sloping from the roof to the tail with a short rear glass, wide hips, squared-off tail with thin LED strips, diffuser, wing and twin exhaust.",
        sheet=dict(style="coupe", length=4.53, width=1.96, height=1.19, wheelbase=2.64, front_overhang=1.03,
                   wheel_diameter=0.67, wheel_width=0.275, track=1.66, ride_height=0.11, nose_z=0.50, cowl_z=0.82,
                   windshield_base=-1.055, roof_front=-0.14, roof_rear=0.25, deck_front=1.95, deck_z=1.02,
                   tail_z=0.98, belt_z=0.80, door_length=1.15, dlo_end=0.47, nose_peak_z=0.33, nose_rollover=0.40,
                   nose_tuck=0.22, rear_glass_length=0.45, max_width_z=0.5, roof_width=1.12,
                   flare=0.06, look="smooth",
                   flare_style="soft", hood_curve=1.6, nose_point=0.2, nose_round=1.6, nose_edge_radius=0.06,
                   tail_edge_radius=0.06, tail_point=0.05, tail_round=3.5, rear_fender_rise=0.08, hip_length=0.7,
                   flare_reach=1.6, vents=["side_intake", "hood_vents"], lamp_shape="blade", lamp_span=0.55,
                   lamp_height=0.06, lamp_drop=0.05, grille="none", intake="split", tail_lamp_style="slim",
                   diffuser=True, diffuser_height=0.32, plate_rear="panel"),
        accessories=dict(wheel=dict(spokes=10, style="mesh"), wing=dict(span=1.7, chord=0.28, height=0.18),
                         exhaust=dict(count=2, spacing=0.16))),
    "boxy_suv": dict(
        description="Square off-roader SUV (4.7 m, 1.85 m tall): flat high hood, flat face with one big round "
                    "headlight a side beside a tall chrome-framed grille, upright windshield and tailgate with the "
                    "rear window cut from behind, flat roof, boxed flares, small square tail lamps low in the bumper.",
        sheet=dict(style="suv", length=4.7, width=1.92, height=1.85, wheelbase=2.85, front_overhang=0.82,
                   wheel_diameter=0.78, wheel_width=0.255, track=1.62, ride_height=0.24, nose_z=1.04, cowl_z=1.12,
                   windshield_base=-1.05, roof_front=-0.80, roof_rear=2.12, deck_front=2.22, deck_z=1.16, tail_z=1.1,
                   belt_z=1.14, max_width_z=0.8, roof_width=1.66, look="angular", flare_style="boxed",
                   hood_curve=1.0, roof_crown=0.01, nose_rake=0.02, tail_rake=0.01, nose_taper=0.96,
                   tail_taper=0.97, nose_point=0.05, nose_round=4.0, tail_point=0.04, tail_round=4.0,
                   nose_edge_radius=0.03, tail_edge_radius=0.03, front_fender_rise=0.0, rear_fender_rise=0.0,
                   arch_cladding=True, lamp_shape="round", round_count=1, lamp_span=0.30, lamp_height=0.13,
                   lamp_drop=0.11, grille="tall", intake="low", tail_lamp_style="square", dlo_corner=0.06,
                   features=[
                       {"name": "rear_window", "remove": True},
                       {"name": "rear_glass", "kind": "recess", "view": "rear", "until": 1.95, "material": "glass",
                        "depth": 0.014, "frame": 0.03, "frame_material": "trim", "frame_depth": 0.006,
                        "outline": {"rect": [0.0, 1.5, 1.3, 0.5, 0.04]}}]),
        accessories=dict(wheel=dict(spokes=6), exhaust=None)),
    "crossover": dict(
        description="Compact crossover (4.55 m, 1.68 m tall): raised hatchback with a high rounded nose, split "
                    "headlights (an LED slit up high, the main lamp low in the bumper), wide chrome-framed grille, "
                    "sloping rear glass, big wheels, boxed flares, a thin full-width tail light bar.",
        sheet=dict(style="suv", length=4.55, width=1.86, height=1.68, wheelbase=2.7, front_overhang=0.9,
                   wheel_diameter=0.76, wheel_width=0.235, track=1.58, ride_height=0.22, nose_z=0.95, cowl_z=1.13,
                   windshield_base=-1.13, roof_front=-0.35, roof_rear=1.55, deck_front=2.05, deck_z=1.2, tail_z=1.1,
                   belt_z=1.1, max_width_z=0.76, roof_width=1.4, flare_style="boxed", flare=0.05, arch_cladding=True,
                   nose_round=3.0, nose_rake=0.10, lamp_shape="split", grille="wide", intake="low",
                   tail_lamp_style="bar", tail_lamp_height=0.04, plate_rear="panel"),
        accessories=dict(wheel=dict(spokes=5))),
    "minivan": dict(
        description="Family minivan (4.95 m): one-box profile with a short sloping nose, large rectangular "
                    "headlights, a small grille slot, long glasshouse, upright tailgate, tall vertical tail lamps.",
        sheet=dict(style="van", length=4.95, width=1.9, height=1.75, wheelbase=3.0, front_overhang=0.95,
                   wheel_diameter=0.68, wheel_width=0.225, track=1.62, ride_height=0.16, nose_z=0.86, cowl_z=1.08,
                   windshield_base=-1.48, roof_front=-0.72, roof_rear=2.12, deck_front=2.32, deck_z=1.15, tail_z=1.08,
                   belt_z=1.04, max_width_z=0.72, roof_width=1.6, window_split=0.36, nose_rake=0.18,
                   lamp_shape="rect", lamp_span=0.48, lamp_height=0.14, lamp_drop=0.09, grille="small",
                   intake="low", tail_lamp_style="vertical", tail_lamp_height=0.6),
        accessories=dict(wheel=dict(spokes=5), exhaust=None)),
}
