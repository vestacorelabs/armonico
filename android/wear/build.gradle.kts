import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// The watch app. It shares the phone app's package name and signing key, which the Wearable Data Layer
// requires, and takes its icon and fonts from the phone app at build time, so each file exists once.
val appRes = rootProject.projectDir.resolve("app/src/main/res")
val generatedRes = layout.buildDirectory.dir("generated/armonico/res")

val copyShared by tasks.registering {
    inputs.dir(appRes)
    outputs.dir(generatedRes)
    doLast {
        val out = generatedRes.get().asFile
        out.deleteRecursively()
        appRes.resolve("mipmap-xxxhdpi/ic_launcher.png").copyTo(out.resolve("mipmap-xxxhdpi/ic_launcher.png"), true)
        appRes.resolve("drawable-nodpi/ic_notify.png").copyTo(out.resolve("drawable-nodpi/ic_notify.png"), true)
        appRes.resolve("drawable-nodpi/logo_icon_dark.png").copyTo(out.resolve("drawable-nodpi/logo_icon_dark.png"), true)
        for (f in listOf("heebo_medium.ttf", "heebo_bold.ttf")) appRes.resolve("font/$f").copyTo(out.resolve("font/$f"), true)
    }
}

android {
    namespace = "org.vestacorelabs.armonico.wear"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.vestacorelabs.armonico"
        minSdk = 30                      // Wear OS 3, the Galaxy Watch4 and later
        targetSdk = 34
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "1.0.1"            // as the phone app
    }

    val keystoreB64 = System.getenv("ANDROID_KEYSTORE_B64")
    signingConfigs {
        if (!keystoreB64.isNullOrBlank()) {
            create("release") {
                val f = layout.buildDirectory.file("armonico.keystore").get().asFile
                f.parentFile.mkdirs()
                f.writeBytes(Base64.getMimeDecoder().decode(keystoreB64))
                storeFile = f
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (!keystoreB64.isNullOrBlank()) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }
    sourceSets["main"].res.srcDir(generatedRes)
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

tasks.named("preBuild") { dependsOn(copyShared) }

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("com.google.android.gms:play-services-wearable:19.0.0")
    implementation("androidx.fragment:fragment-ktx:1.8.5")   // Play services brings an old one, which lint refuses
    implementation("androidx.wear:wear-remote-interactions:1.1.0")
    // the Tile (a screen beside the watch face) and the Complication (a small item on the watch face)
    implementation("androidx.wear.tiles:tiles:1.4.1")
    implementation("androidx.wear.protolayout:protolayout:1.2.1")
    implementation("androidx.wear.protolayout:protolayout-expression:1.2.1")
    implementation("androidx.wear.watchface:watchface-complications-data-source:1.2.1")
    implementation("com.google.guava:guava:33.3.1-android")
}
