#include <Arduino.h>
#include <TFT_eSPI.h>
#include <SPI.h>
#include <lvgl.h>
#include <ESP32Encoder.h>
#include "ui.h"

//#define SPI_FREQUENCY  27000000
#define NUM_DISPLAYS 3
#define BUFFER_SIZE (160 * 160 / 13)
#define DISPLAY_CHECK_INTERVAL 100  // Interval for checking display status in ms

static const uint16_t screenWidth = 160;
static const uint16_t screenHeight = 160;

// Structures for each display
TFT_eSPI lcds[NUM_DISPLAYS] = {TFT_eSPI(), TFT_eSPI(), TFT_eSPI()};
static lv_color_t draw_bufs[NUM_DISPLAYS][BUFFER_SIZE];
static lv_color_t draw_bufs2[NUM_DISPLAYS][BUFFER_SIZE];
lv_disp_draw_buf_t lv_draw_bufs[NUM_DISPLAYS];
lv_disp_drv_t lv_disp_drivers[NUM_DISPLAYS];
lv_disp_t* displays[NUM_DISPLAYS];

// ENCODER
const int ENCODER_PINS[NUM_DISPLAYS][3] = {
    //{11, 8, 14},
    //{10, 3, 13},
    //{9, 46, 12}

    {11, 14, 8},
    {10, 13, 3},
    {9, 12, 46}
};

ESP32Encoder encoders[NUM_DISPLAYS];

// VOLUME
int volumes[NUM_DISPLAYS] = {50, 50, 50};
bool is_muted[NUM_DISPLAYS] = {false, false, false};
int volume_before_mute[NUM_DISPLAYS] = {50, 50, 50};
bool display_dirty[NUM_DISPLAYS] = {true, true, true};
int last_sent_volume[NUM_DISPLAYS] = { -1, -1, -1 };

int cs_pins[NUM_DISPLAYS] = {15, 16, 17};  // CS pins for 3 displays
static int display_indices[NUM_DISPLAYS] = {0, 1, 2};  // Indices for displays
static bool display_initialized[NUM_DISPLAYS] = {false, false, false};

void selectDisplay(int csPin) {
    if (csPin < 0) {
        Serial.println("CS Pin invalido");
        return;
    }

    for (int i = 0; i < NUM_DISPLAYS; i++) {
        if (cs_pins[i] >= 0) {
            digitalWrite(cs_pins[i], HIGH);
        }
    }
    digitalWrite(csPin, LOW);
}

void my_disp_flush(lv_disp_drv_t *disp, const lv_area_t *area, lv_color_t *color_p) {
    if (!disp || !disp->user_data || !area || !color_p) {
        Serial.println("Invalid flush parameters");
        return;
    }

    int index = *(int *)(disp->user_data);
    if (index < 0 || index >= NUM_DISPLAYS) {
        Serial.println("Invalid display index");
        lv_disp_flush_ready(disp);
        return;
    }

    uint32_t w = (area->x2 - area->x1 + 1);
    uint32_t h = (area->y2 - area->y1 + 1);

    if (w == 0 || h == 0 || w > screenWidth || h > screenHeight) {
        Serial.println("Invalid area dimensions");
        lv_disp_flush_ready(disp);
        return;
    }

    selectDisplay(cs_pins[index]);

    lcds[index].startWrite();
    lcds[index].setAddrWindow(area->x1, area->y1, w, h);
    lcds[index].pushColors((uint16_t *)&color_p->full, w * h, true);
    lcds[index].endWrite();

    lv_disp_flush_ready(disp);
}


bool setup_display(int index) {
    if (index < 0 || index >= NUM_DISPLAYS) {
        Serial.println("Invalid display index in setup");
        return false;
    }

    // pinMode(cs_pins[index], OUTPUT);
    digitalWrite(cs_pins[index], LOW);

    lcds[index].begin();

    lcds[index].setRotation(0);
    lcds[index].fillScreen(TFT_BLACK);

    // Verify buffer allocation
    if (!draw_bufs[index] || !draw_bufs2[index]) {
        Serial.println("Buffer allocation failed");
        return false;
    }

    lv_disp_draw_buf_init(&lv_draw_bufs[index], draw_bufs[index], draw_bufs2[index], BUFFER_SIZE);
    lv_disp_drv_init(&lv_disp_drivers[index]);

    lv_disp_drivers[index].hor_res = screenWidth;
    lv_disp_drivers[index].ver_res = screenHeight;
    lv_disp_drivers[index].flush_cb = my_disp_flush;
    lv_disp_drivers[index].draw_buf = &lv_draw_bufs[index];
    lv_disp_drivers[index].user_data = &display_indices[index];

    // Register display driver and save display handle
    displays[index] = lv_disp_drv_register(&lv_disp_drivers[index]);

    if (!displays[index]) {
        Serial.println("Display driver registration failed");
        return false;
    }

    display_initialized[index] = true;
    return true;
}

void setup() {
    Serial.begin(115200);
    
    pinMode(18, OUTPUT);
    digitalWrite(18, HIGH);
    
    pinMode(15, OUTPUT);
    pinMode(16, OUTPUT);
    pinMode(17, OUTPUT);

    digitalWrite(15, HIGH);
    digitalWrite(16, HIGH);
    digitalWrite(17, HIGH);

    lv_init();

    // Encoder pinMode e attach
    for (int i = 0; i < NUM_DISPLAYS; i++) {
        pinMode(cs_pins[i], OUTPUT);
        digitalWrite(cs_pins[i], HIGH);
        pinMode(ENCODER_PINS[i][0], INPUT);
        pinMode(ENCODER_PINS[i][1], INPUT);
        pinMode(ENCODER_PINS[i][2], INPUT);
        encoders[i].attachHalfQuad(ENCODER_PINS[i][0], ENCODER_PINS[i][1]);
        encoders[i].setCount(volumes[i]);
    }

    // Initialize each display and register them
    for (int i = 0; i < NUM_DISPLAYS; i++) {
        if (!setup_display(i)) {
            Serial.println("Display setup failed");
            continue;
        }

        // Use lv_disp_set_default() with the correct display handle
        lv_disp_set_default(displays[i]);

        LV_EVENT_GET_COMP_CHILD = lv_event_register_id();

        lv_disp_t * dispp = lv_disp_get_default();
        lv_theme_t * theme = lv_theme_default_init(dispp, lv_palette_main(LV_PALETTE_BLUE), lv_palette_main(LV_PALETTE_RED), true, LV_FONT_DEFAULT);
        lv_disp_set_theme(dispp, theme);

        // Load different screens on each display
        if (i == 0) {
            ui_Screen3_screen_init();
            lv_disp_load_scr(ui_Screen3);  // Load Screen1 on Display 0
        } else if (i == 1) {
            ui_Screen2_screen_init();
            lv_disp_load_scr(ui_Screen2);  // Load Screen2 on Display 1
        } else if (i == 2) {
            ui_Screen1_screen_init();
            lv_disp_load_scr(ui_Screen1);  // Load Screen3 on Display 2
        }
    }  
}

void loop() {
    lv_timer_handler();

    for (int i = 0; i < NUM_DISPLAYS; i++) {
        // ENCODER movimento
        long count = encoders[i].getCount();
        if (count != 0) {
            volumes[i] += count;
            encoders[i].clearCount();
            volumes[i] = constrain(volumes[i], 0, 100);
            display_dirty[i] = true;
            if (volumes[i] != last_sent_volume[i]) {
                Serial.printf("SET_VOL:%d,%d\n", i, volumes[i]);
                last_sent_volume[i] = volumes[i];
            }
        }

        // ENCODER click
        if (digitalRead(ENCODER_PINS[i][2]) == LOW) {
            is_muted[i] = !is_muted[i];
            if (is_muted[i]) {
                volume_before_mute[i] = volumes[i];
                volumes[i] = 0;
            } else {
                volumes[i] = volume_before_mute[i];
            }
            display_dirty[i] = true;
            Serial.printf("MUTE:%d,%d\n", i, is_muted[i]);
            delay(250); // debounce
        }

        // Aggiornamento display solo se necessario
        if (display_dirty[i]) {
            lv_disp_set_default(displays[i]);

            if (i == 0) {
                lv_arc_set_value(ui_slider1, volumes[i]);
                lv_label_set_text_fmt(ui_percentuale1, "%d%%", volumes[i]);
            } else if (i == 1) {
                lv_arc_set_value(ui_slider2, volumes[i]);
                lv_label_set_text_fmt(ui_percentuale2, "%d%%", volumes[i]);
            } else if (i == 2) {
                lv_arc_set_value(ui_slider3, volumes[i]);
                lv_label_set_text_fmt(ui_percentuale3, "%d%%", volumes[i]);
            }

            display_dirty[i] = false;
        }
    }

    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();

        if (cmd.startsWith("VOLS:")) {
            String values = cmd.substring(5);
            int vol1, vol2, vol3;
            
            if (sscanf(values.c_str(), "%d,%d,%d", &vol1, &vol2, &vol3) == 3) {
                volumes[0] = constrain(vol1, 0, 100);
                volumes[1] = constrain(vol2, 0, 100);
                volumes[2] = constrain(vol3, 0, 100);
                
                encoders[0].setCount(volumes[0]);
                encoders[1].setCount(volumes[1]);
                encoders[2].setCount(volumes[2]);
                
                // CRITICO: Cancella i count per evitare interferenze
                encoders[0].clearCount();
                encoders[1].clearCount();
                encoders[2].clearCount();
                
                // ESSENZIALE: Aggiorna per evitare loop
                last_sent_volume[0] = volumes[0];
                last_sent_volume[1] = volumes[1];
                last_sent_volume[2] = volumes[2];
                
                for (int i = 0; i < 3; i++) {
                    display_dirty[i] = true;
                }
                
                Serial.printf("VOLS applicato: %d,%d,%d\n", volumes[0], volumes[1], volumes[2]);
            }
        } else if (cmd.startsWith("MUTE:")) {
            int sep = cmd.indexOf(',');
            if (sep > 0) {
                int idx = cmd.substring(5, sep).toInt();
                int muteState = cmd.substring(sep + 1).toInt();
                if (idx >= 0 && idx < NUM_DISPLAYS) {
                    is_muted[idx] = muteState;
                    if (is_muted[idx]) {
                        volume_before_mute[idx] = volumes[idx];
                        volumes[idx] = 0;
                    } else {
                        volumes[idx] = volume_before_mute[idx];
                    }
                    display_dirty[idx] = true;
                    Serial.printf("Comando ricevuto: MUTE:%d,%d\n", idx, muteState);
                } else {
                    Serial.printf("Applicazione per indice %d non trovata per il comando MUTE.\n", idx);
                }
            }
        } else {
            Serial.printf("Comando ricevuto: '%s'\n", cmd.c_str());
        }
    }

    delay(5);
}
