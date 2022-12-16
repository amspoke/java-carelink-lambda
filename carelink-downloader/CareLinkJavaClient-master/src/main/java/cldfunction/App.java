package cldfunction;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URL;
import java.util.HashMap;
import java.util.Map;
import java.util.stream.Collectors;

import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestHandler;
import com.amazonaws.services.lambda.runtime.events.APIGatewayProxyRequestEvent;
import com.amazonaws.services.lambda.runtime.events.APIGatewayProxyResponseEvent;

// S3 management
import software.amazon.awssdk.auth.credentials.ProfileCredentialsProvider;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectResponse;
import software.amazon.awssdk.services.s3.model.S3Exception;

/* Specifics for the CareLink Library */

import com.google.gson.GsonBuilder;
import info.nightscout.medtronic.carelink.client.CareLinkClient;
import info.nightscout.medtronic.carelink.message.*;
import org.apache.commons.cli.*;

import java.io.FileWriter;
import java.nio.file.Paths;
import java.text.SimpleDateFormat;
import java.util.Calendar;

import java.io.File;
import java.io.FileInputStream;

/**
 * Handler for requests to Lambda function.
 */
public class App implements RequestHandler<APIGatewayProxyRequestEvent, APIGatewayProxyResponseEvent> {

    public APIGatewayProxyResponseEvent handleRequest(final APIGatewayProxyRequestEvent input, final Context context) {
        Map<String, String> headers = new HashMap<>();
        headers.put("Content-Type", "application/json");
        headers.put("X-Custom-Header", "application/json");

        APIGatewayProxyResponseEvent response = new APIGatewayProxyResponseEvent()
                .withHeaders(headers);
        try {
            String output = String.format("{ \"message\": \"hello world\", \"location\": \"%s\" }","ok");

            updateCareLink();

            return response
                    .withStatusCode(200)
                    .withBody(output);
        } finally {}
    }

    private static void updateCareLink()
    {

        String folder;
        String s3Bucket;
        int repeat;
        int wait;
        boolean downloadSession;
        boolean downloadRecentData;
        boolean verbose;
        boolean anonymize;
        boolean dumpJsonException;

        try {
            //Set params
            verbose = Boolean.parseBoolean(System.getenv("OPTION_VERBOSE"));
            downloadSession = Boolean.parseBoolean(System.getenv("OPTION_SESSION"));
            downloadRecentData = Boolean.parseBoolean(System.getenv("OPTION_DATA"));
            anonymize = Boolean.parseBoolean(System.getenv("OPTION_ANONYM"));
            dumpJsonException = Boolean.parseBoolean(System.getenv("OPTION_JSON_EXCEPTION"));
            // folder = "/tmp/";
            folder = System.getenv("OPTION_FOLDER");
            s3Bucket = System.getenv("OPTION_S3BUCKET");
            repeat = (Boolean.parseBoolean(System.getenv("OPTION_REPEAT"))) ? Integer.parseInt(System.getenv("OPTION_REPEAT")) : 1;
            wait = (Boolean.parseBoolean(System.getenv("OPTION_WAIT"))) ? Integer.parseInt(System.getenv("OPTION_WAIT")) : 1;
            //Execute client
            callCareLinkClient(
                    verbose,
                    System.getenv("OPTION_USERNAME"), System.getenv("OPTION_PASSWORD"), System.getenv("OPTION_COUNTRY"),
                    downloadSession, downloadRecentData,
                    anonymize,
                    folder,
                    s3Bucket,
                    repeat, wait,
                    dumpJsonException);
        } catch (Exception ex) {
            System.out.println(ex.getMessage());
        }
    }

    private static void callCareLinkClient(boolean verbose, String username, String password, String country, Boolean downloadSessionInfo, Boolean downloadData, boolean anonymize, String folder, String s3Bucket, int repeat, int wait, boolean dumpJsonException){

        CareLinkClient client = null;
        RecentData recentData = null;

        client = new CareLinkClient(username, password, country);
        if(verbose)printLog("Client created!");

        if(client.login()) {

            for(int i = 0; i < repeat; i++) {
                if (verbose) printLog("Starting download, count:  " + String.valueOf(i + 1));
                //Session info is requested
                if (downloadSessionInfo) {
                    writeJson(client.getSessionUser(), folder, "", "user", anonymize, verbose);
                    writeJson(client.getSessionProfile(), folder, "", "profile", anonymize, verbose);
                    writeJson(client.getSessionCountrySettings(), "", folder, "country", anonymize, verbose);
                    writeJson(client.getSessionMonitorData(), folder, "", "monitor", anonymize, verbose);
                }
                //Recent data is requested
                if(downloadData) {
                    try {
                        for(int j = 0; j < 2; j++) {
                            recentData = client.getRecentData();
                            //Auth error
                            if(client.getLastResponseCode() == 401) {
                                printLog("GetRecentData login error (response code 401). Trying again in 1 sec!");
                                Thread.sleep(1000);
                            }
                            //Get success
                            else if(client.getLastResponseCode() == 200) {
                                //Data OK
                                if(client.getLastDataSuccess()) {
                                    writeJson(recentData, folder, s3Bucket, "data", anonymize, verbose);
                                //Data error
                                } else {
                                    printLog("Data exception: " + (client.getLastErrorMessage() == null ? "no details available" : client.getLastErrorMessage()));
                                    if(dumpJsonException){
                                        writeFile(client.getLastResponseBody(), folder, "dataex", verbose);
                                    }
                                }
                                //STOP!!!
                                break;
                            } else  {
                                printLog("Error, response code: " + String.valueOf(client.getLastResponseCode()) + " Trying again in 1 sec!");
                                Thread.sleep(1000);
                            }
                        }
                    } catch (Exception ex) {
                        System.out.println(ex.getMessage());
                    }
                }
                try {
                    if(i < repeat - 1) {
                        if (verbose) printLog("Waiting " + String.valueOf(wait) + " minutes before next download!");
                        Thread.sleep(wait * 60000);
                    }
                } catch (Exception ex){ }
            }
        } else {
            printLog("Client login error! Response code: " + String.valueOf(client.getLastResponseCode()) + " Error message: " + client.getLastErrorMessage());
        }

    }

    protected static void writeJson(Object object, String folder, String s3Bucket, String name, boolean anonymize, boolean verbose){

        String content;

        //Anonymize data
        if(anonymize) {
            anonymizeData(object);
        }

        //Convert JSON to string and write to file
        try {
            content = new GsonBuilder().setDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX").setPrettyPrinting().create().toJson(object);
            // I need to write the file first, and after that i need to copy it to S3!!!
            // writeFile(content, folder, name, verbose);
            writeFileToS3(content, s3Bucket, name, verbose);
        } catch (Exception ex) {
            printLog("Error during save of " + name + " . Details: " + ex.getClass().getName() + " - " + ex.getMessage());
        }

    }


    protected static void writeFile(String content, String folder, String name, boolean verbose){

        FileWriter writer = null;
        SimpleDateFormat sdfDate = new SimpleDateFormat("yyyyMMdd_HHmmss");
        String filename = name + "-" + sdfDate.format(Calendar.getInstance().getTime()) + ".json";

        try {
            if(folder == null)
                writer = new FileWriter(filename);
            else
                writer = new FileWriter(Paths.get(folder, filename).toAbsolutePath().toString());
            writer.write(content);
            writer.flush();
            writer.close();
            if (verbose) printLog(name + " saved!");
        } catch (Exception ex) {
            printLog("Error during save of " + name + " . Details: " + ex.getClass().getName() + " - " + ex.getMessage());
        }

    }

    /* This function is exactly the same than writeFile but also it uploads it to the s3 Bucket  */
    protected static void writeFileToS3(String content, String bucket_name, String name, boolean verbose){

        // Temp file folder (this is hardcoded as it's specific to the container)
        String folder = "/tmp";
        FileWriter writer = null;
        SimpleDateFormat sdfDate = new SimpleDateFormat("yyyyMMdd_HHmmss");
        String filename = name + "-" + sdfDate.format(Calendar.getInstance().getTime()) + ".json";

        try {
            if(folder == null)
                writer = new FileWriter(filename);
            else
                writer = new FileWriter(Paths.get(folder, filename).toAbsolutePath().toString());

            writer.write(content);
            writer.flush();
            writer.close();
            if (verbose) printLog(name + " saved!");
            
            // Copy written file to S3
            System.out.format("Uploading %s/%s to S3 bucket %s...\n", folder, filename, bucket_name);
            S3Client s3 = S3Client.builder()
                .region(Region.EU_SOUTH_2)
                .build();
            // String result = putS3Object(s3, bucket_name, folder+"/"+filename, "/cld/");
            PutObjectRequest request = PutObjectRequest.builder().bucket(bucket_name).key(filename).build();
            s3.putObject(request, RequestBody.fromFile(new File(folder+"/"+filename)));
            System.out.println("Tag information: "+request.toString());
            s3.close();

        } catch (Exception ex) {
            printLog("Error during save of " + name + " . Details: " + ex.getClass().getName() + " - " + ex.getMessage());
        }

    }

    // snippet-start:[s3.java2.s3_object_upload.main]
    public static String putS3Object(S3Client s3, String bucketName, String objectKey, String objectPath) {

        try {
            Map<String, String> metadata = new HashMap<>();
            metadata.put("x-amz-meta-myVal", "test");
            PutObjectRequest putOb = PutObjectRequest.builder()
                .bucket(bucketName)
                .key(objectKey)
                .metadata(metadata)
                .build();

            PutObjectResponse response = s3.putObject(putOb, RequestBody.fromBytes(getObjectFile(objectPath)));
            return response.eTag();

        } catch (S3Exception e) {
            System.err.println(e.getMessage());
            System.exit(1);
        }

        return "";
    }

   // Return a byte array.
   private static byte[] getObjectFile(String filePath) {

    FileInputStream fileInputStream = null;
    byte[] bytesArray = null;

    try {
        File file = new File(filePath);
        bytesArray = new byte[(int) file.length()];
        fileInputStream = new FileInputStream(file);
        fileInputStream.read(bytesArray);

    } catch (IOException e) {
        e.printStackTrace();
    } finally {
        if (fileInputStream != null) {
            try {
                fileInputStream.close();
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }

    return bytesArray;
}

    protected static void anonymizeData(Object object){

        User user;
        Profile profile;
        RecentData recentData;

        if(object != null){
            if(object instanceof User){
                user = (User) object;
                user.accountId = 99999999;
                user.id = String.valueOf(user.accountId);
                user.lastName = "LastName";
                user.firstName = "FirstName";
            } else if(object instanceof Profile){
                profile = (Profile) object;
                profile.address = "Address";
                profile.firstName = "FirstName";
                profile.lastName = "LastName";
                profile.middleName = "MiddleName";
                profile.dateOfBirth  = "1900-01-01";
                profile.city = "City";
                profile.email = "email@email.email";
                profile.parentFirstName = "ParentFirstName";
                profile.parentLastName = "ParentLastName";
                profile.phone = "+00-00-000000";
                profile.phoneLegacy = "+00-00-000000";
                profile.postalCode = "9999";
                profile.patientNickname = "Nickname";
                profile.stateProvince = "State";
                profile.username = "Username";
            } else if(object instanceof RecentData){
                recentData = (RecentData) object;
                recentData.firstName = "FirstName";
                recentData.lastName = "LastName";
                recentData.medicalDeviceSerialNumber = "SN9999999X";
                recentData.conduitSerialNumber = "XXXXXX-XXXX-XXXX-XXXX-9999-9999-9999-9999";
            }
        }


    }

    protected static void printLog(String logText){
        System.out.println(new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss").format(Calendar.getInstance().getTime()) + " " + logText);
    }


}
