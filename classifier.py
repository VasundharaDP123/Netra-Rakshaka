def classify_strain(data):
    score = 0

    if data["blink_rate"] < 6:       score += 40
    elif data["blink_rate"] < 10:    score += 20

    if data["screen_distance_cm"] < 20:   score += 25
    elif data["screen_distance_cm"] < 30: score += 10

    if data["eye_temp_celsius"] < 34.0:  score += 15

    if data["room_humidity_pct"] < 28:   score += 10

    if data["ambient_lux"] < 100:        score += 10

    if data["head_tilt_degrees"] > 35:   score += 10

    if score >= 60:   
        return "Critical", score
    elif score >= 30: 
        return "Moderate", score
    
    return "Safe", score
